"""街景照片 → 候選人看板偵測 → 財產摘要疊圖（原型）。

用法：
  .venv/bin/python pipeline/billboard.py run     IMG.jpeg --out work/   # detect → facts → render 一次跑完
  .venv/bin/python pipeline/billboard.py detect  IMG.jpeg --out work/   # Gemini ER 2 找廣告面＋讀姓名，寫 detect.json
  .venv/bin/python pipeline/billboard.py facts   work/detect.json       # 對 115 年候選人，算不動產（所在縣市分區／外縣市）、房貸、有價證券、含房產淨資產
  .venv/bin/python pipeline/billboard.py render  IMG.jpeg work/facts.json

規則（2026-10-18 使用者裁決）：彩色區域用 bbox；同名多人用縣市縮小（廣告職務文字→同照片其他已對到者的縣市）；
對不到 DB 的（里長、非本屆候選人站台）維持黑白、不出卡片；所在縣市分到區；有價證券取全部；卡片放含房產估值的淨資產。
"""
import argparse, functools, io, json, os, re, sqlite3, threading
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data/db/candidate_wealth.sqlite"
DIST = ROOT / "dist"
FONT = os.environ.get("BILLBOARD_FONT") or str(Path.home() / "Library/Fonts/NotoSansTC-VariableFont_wght.ttf")
FONT_INDEX = int(os.environ.get("BILLBOARD_FONT_INDEX", "0"))
FONT_BOLD = os.environ.get("BILLBOARD_FONT_BOLD")
MAX_EDGE = 2000
ER_MODEL = "gemini-robotics-er-2-preview"
COUNTIES = ["臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市", "嘉義市",
            "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
            "臺東縣", "澎湖縣", "金門縣", "連江縣"]
# 議員選舉區 → 行政區（原型只放臺北市；規模化要補全台，來源：中選會選舉區劃分）
COUNCIL_DISTRICTS = {
    ("臺北市", "臺北市第1選舉區"): ["北投區", "士林區"], ("臺北市", "臺北市第2選舉區"): ["內湖區", "南港區"],
    ("臺北市", "臺北市第3選舉區"): ["松山區", "信義區"], ("臺北市", "臺北市第4選舉區"): ["中山區", "大同區"],
    ("臺北市", "臺北市第5選舉區"): ["中正區", "萬華區"], ("臺北市", "臺北市第6選舉區"): ["大安區", "文山區"],
}


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold and FONT_BOLD else FONT, size, index=FONT_INDEX)


def prepare(src):
    """路徑或 bytes → EXIF 轉正、RGB、長邊縮到 2000 px。之後 detect／facts／compose 都用這張圖的座標。"""
    im = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    im.draft("RGB", (MAX_EDGE, MAX_EDGE))  # JPEG 直接以 1/2、1/4 尺度解碼，省記憶體；非 JPEG 無作用
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((MAX_EDGE, MAX_EDGE))
    return im


# ---------------------------------------------------------------- detect
DETECT_PROMPT = """這是一張台灣街景照片。找出畫面中所有「選舉競選廣告」的印刷廣告面：候選人看板、布條、旗幟、公車車體上的候選人廣告貼圖，上面有候選人的臉或姓名。
規則：
1. 每一位候選人的每一面廣告各自一筆；兩面相鄰、各印不同候選人的看板要分成兩筆，不要合併。
2. 框要緊貼印刷廣告面本身：公車廣告只框貼有候選人照片／姓名的那塊車身面板，不要包含車窗以外的車體、車輪、下方路面、前方行人；看板不要框到支架或建築。
3. 不要框商店招牌、商業廣告、路標。
4. 一面廣告上印了多位政治人物（合照、聯合看板、站台背書）仍算一筆，但 names 要把每個人分開列出，一人一個元素；不要把多個名字接成一個字串。
每筆回傳：box_2d [ymin,xmin,ymax,xmax]（0–1000）、names（廣告上印的所有人名，照印刷文字，被遮住就只寫看得到的字；單人也放成一個元素的陣列）、office（廣告上的職務／選區文字，沒有就空字串）、confidence（0–1）。只輸出 JSON 陣列。"""

# 注意：names 不能加 minItems——ER 2 的 constrained decoding 會卡住到逾時（2026-10-18 對照實驗：有 minItems 60 s 無回應，拿掉 6.7 s）
DETECT_SCHEMA = {"type": "array", "items": {"type": "object", "properties": {
    "box_2d": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
    "names": {"type": "array", "items": {"type": "string"}}, "office": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["box_2d", "names", "office", "confidence"]}}
_NAME_SEP = re.compile(r"[、，,／/｜|・·與和及\s]+")


def split_names(it):
    """names 陣列優先；舊格式或模型仍把多人接成一串時，用分隔符拆開。"""
    raw = it.get("names") or [it.get("name", "")]
    names = []
    for n in raw:
        for part in _NAME_SEP.split(str(n)):
            part = re.sub(r"[^\u4e00-\u9fff]", "", part)
            if len(part) >= 2 and part not in names:
                names.append(part)
    return names or [re.sub(r"[^\u4e00-\u9fff]", "", str(raw[0]))]


_client = None
_client_lock = threading.Lock()


def _get_client():
    """模組層級 lazy singleton：暫時建立的 Client 被回收後會出現 'client has been closed'。
    加鎖：併發的第一批請求若各自建立 Client，被覆蓋的那些會在請求途中被回收關閉。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from google import genai
                from google.genai import types
                # 逾時單位毫秒（與 ocr_batch.py 同寫法）；沒設的話 Gemini 偶爾掛住會一路等到 Cloud Run 60 s 上限變 504。25 s × 2 次嘗試要壓在 Cloud Run 60 s 內
                _client = genai.Client(http_options=types.HttpOptions(timeout=int(os.environ.get("GEMINI_TIMEOUT_MS", "25000"))))
    return _client


class DetectTimeout(Exception):
    pass


def box_px(it, W, H):
    y0, x0, y1, x1 = it["box_2d"]
    return [round(x0 / 1000 * W), round(y0 / 1000 * H), round(x1 / 1000 * W), round(y1 / 1000 * H)]


def detect_image(im):
    """prepare() 後的圖 → Gemini ER 2 偵測結果（含此圖座標系的 box_px）。"""
    from google.genai import types

    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
    r = None
    for attempt in range(2):  # 逾時重試一次
        try:
            r = _get_client().models.generate_content(
                model=ER_MODEL,
                contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), DETECT_PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=DETECT_SCHEMA, temperature=0),
            )
            break
        except Exception as e:
            import httpx
            # httpx.TimeoutException（ReadTimeout 等）與 ConnectError 都是 TransportError：暫時性，重試一次後以逾時回報
            transient = isinstance(e, httpx.TransportError)
            if transient and attempt == 0:
                continue
            raise DetectTimeout(f"{type(e).__name__}: {e}") if transient else e
    items = json.loads(r.text)
    for it in items:
        it["names"] = split_names(it)
        it["name"] = "、".join(it["names"])
        it["box_px"] = box_px(it, *im.size)
    return items


def detect(img_path, out_dir):
    im = prepare(img_path)
    W, H = im.size
    items = detect_image(im)
    out = {"image": str(img_path), "size": [W, H], "model": ER_MODEL, "detections": items}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "detect.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    dbg = im.copy(); d = ImageDraw.Draw(dbg)
    f = font(36)
    for it in items:
        x0, y0, x1, y1 = it["box_px"]
        d.rectangle([x0, y0, x1, y1], outline="red", width=4)
        d.text((x0 + 4, y0 + 4), it["name"], fill="red", font=f)
    dbg.save(out_dir / "detect_debug.jpg", quality=85)
    print(json.dumps([(i["name"], i["office"], i["box_2d"], i["confidence"]) for i in items], ensure_ascii=False))
    return out


# ---------------------------------------------------------------- facts
COLS = ["seq", "name", "city", "elect_type", "district", "party", "declare_date"]


def county_of(loc):
    for c in COUNTIES:
        if loc.startswith(c):
            return c
    return loc[:3]


def city_in_text(t):
    t = t.replace("台", "臺")
    return next((c for c in COUNTIES if c in t or c[:2] in t), None)


def candidates_for(con, name):
    name = re.sub(r"[^一-鿿]", "", name)
    if len(name) < 2:
        return []
    rows = con.execute(f"select {','.join(COLS)} from candidates where year=115 and name=?", (name,)).fetchall()
    if not rows:
        rows = con.execute(f"select {','.join(COLS)} from candidates where year=115 and name like ?", (name + "%",)).fetchall()
    return [dict(zip(COLS, r)) for r in rows]


def match_all(con, detections):
    """兩輪：先收唯一命中者，取其縣市當照片所在地；再用廣告職務文字或照片縣市解同名多人。"""
    pools = [candidates_for(con, d["name"]) for d in detections]
    photo_city = Counter(p[0]["city"] for p in pools if len(p) == 1).most_common(1)
    photo_city = photo_city[0][0] if photo_city else None
    out = []
    for d, pool in zip(detections, pools):
        how = "unique"
        if len(pool) > 1:
            city = city_in_text(d.get("office", "")) or photo_city
            narrowed = [p for p in pool if p["city"] == city] if city else []
            if len(narrowed) == 1:
                pool, how = narrowed, f"narrowed by city {city}"
        if len(pool) == 1:
            out.append({"match": pool[0], "how": how})
        elif pool:
            out.append({"match": None, "how": "ambiguous", "candidates": pool})
        else:
            out.append({"match": None, "how": "not in db"})
    return out


@functools.lru_cache(maxsize=1)
def _data_json():
    return json.load(open(DIST / "data.json"))


def load_detail(seq):
    rec = next(r for r in _data_json() if r["year"] == "115" and r["id"] == seq)
    return rec, json.load(open(DIST / f"detail/{rec['shard']}.json"))[seq]


def facts_for(cand):
    rec, det = load_detail(cand["seq"])
    home = cand["city"]
    con = sqlite3.connect(DB)
    notes = {i: (pw or "") for i, pw in con.execute("select idx, price_as_written from buildings where year=115 and seq=?", (cand["seq"],))}
    groups = {}
    for kind, rows in (("bldg", det.get("bldg", [])), ("land", det.get("land", []))):
        for r in rows:
            c = county_of(r["loc"])
            town = r["loc"][len(c):len(c) + 3] if c in COUNTIES else ""
            key = (c, town) if c == home else (c, "")  # 所在縣市分到區；外縣市只到縣市
            g = groups.setdefault(key, {"county": c, "town": town if c == home else "", "home": c == home, "units": 0,
                                        "land_rows": 0, "land_m2": 0.0, "est": 0, "lo": 0, "hi": 0, "towns": set(), "roles": set()})
            g["towns"].add(town); g["roles"].add(r["role"])
            if kind == "bldg":
                if "共同使用" not in notes.get(r["i"], ""):  # 公設列不算一戶
                    g["units"] += 1
            else:
                g["land_rows"] += 1; g["land_m2"] += r["own_area"]
            if r.get("cnt"):
                g["est"] += r["est"]; g["lo"] += r["lo"]; g["hi"] += r["hi"]
    for g in groups.values():
        g["towns"] = sorted(g["towns"]); g["roles"] = sorted(g["roles"])
    s = rec["sum"]
    return {
        "cand": cand, "home": home, "elect_towns": COUNCIL_DISTRICTS.get((home, cand["district"])), "declare_date": rec.get("date"),
        "realestate": sorted(groups.values(), key=lambda g: (not g["home"], -g["est"])),
        "re_est": s["re_est"], "re_lo": s["re_lo"], "re_hi": s["re_hi"],
        "mortgage": s["mortgage"], "debt": s["debt"],
        "securities": s["stock_mkt"] + s["fund_mkt"] + s["other_sec"],
        "stock_mkt": s["stock_mkt"], "fund_mkt": s["fund_mkt"], "other_sec": s["other_sec"],
        "deposit_std": s["deposit_std"], "net_mkt": s["net_mkt"], "net_with_re": s["net_mkt"] + s["re_est"],
    }


def facts_for_detections(detections):
    """一面廣告多人 → 每人一筆結果（共用同一個 detection／框）。"""
    con = sqlite3.connect(DB)
    persons = [(it, n) for it in detections for n in (it.get("names") or split_names(it))]
    pseudo = [{"name": n, "office": it.get("office", "")} for it, n in persons]
    out = []
    for (it, n), m in zip(persons, match_all(con, pseudo)):
        e = {"detection": it, "person": n, **m}
        if m["match"]:
            e["facts"] = facts_for(m["match"])
        out.append(e)
    return out


def facts(detect_json):
    det = json.load(open(detect_json))
    out = det["results"] = facts_for_detections(det["detections"])
    Path(detect_json).with_name("facts.json").write_text(json.dumps(det, ensure_ascii=False, indent=1))
    for e in out:
        f = e.get("facts")
        print(e["person"], e["how"], "→", f and (f["cand"]["name"], f["cand"]["elect_type"], f["cand"]["district"],
              "net_with_re", round(f["net_with_re"]), "re", f["re_est"], "mortgage", f["mortgage"], "sec", round(f["securities"])))
    return det


# ---------------------------------------------------------------- render
def wan(v):
    return f"{v/1e4:,.0f} 萬"


def card_lines(f):
    c = f["cand"]
    sub = f"{c['elect_type']}候選人 · {c['district']}"
    if f["elect_towns"]:
        sub += f"（{'、'.join(f['elect_towns'])}）"
    lines = [(c["name"], "title"), (sub, "sub"), (f"淨資產（含房產估值）{wan(f['net_with_re'])}", "big")]

    def desc(g):
        parts = []
        if g["units"]:
            parts.append(f"{g['units']} 戶")
        if g["land_rows"] and not g["units"]:
            m2 = g["land_m2"]
            parts.append(f"土地 {g['land_rows']} 筆 {m2:.2f} m²" if m2 < 1 else f"土地 {g['land_rows']} 筆 {m2:.0f} m²")
        where = g["county"] + g["town"] if g["home"] else g["county"] + "／".join(t for t in g["towns"] if t)
        s = f"{where}：" + "、".join(parts)
        if g["est"] >= 1e4:
            s += f"，估 {wan(g['est'])}"
        if g["home"] and f["elect_towns"] and g["town"] in f["elect_towns"]:
            s += "（選區內）"
        return s

    home = [g for g in f["realestate"] if g["home"]]
    away = [g for g in f["realestate"] if not g["home"]]
    lines.append((f"所在縣市（{f['home']}）", "h"))
    lines += [(desc(g), "b") for g in home] or [("無不動產", "b")]
    if home and f["elect_towns"] and not any(g["town"] in f["elect_towns"] for g in home):
        lines.append((f"選區（{'、'.join(f['elect_towns'])}）內無不動產", "note"))
    lines.append(("外縣市", "h"))
    lines += [(desc(g), "b") for g in away] or [("無不動產", "b")]
    lines.append(("", "gap"))
    lines.append((f"房貸餘額 {wan(f['mortgage'])}" if f["mortgage"] else "無房貸", "b"))
    sec = f"有價證券 {wan(f['securities'])}"
    if f["securities"] - f["stock_mkt"] >= 1e4:
        sec += f"（股票 {wan(f['stock_mkt'])}）"
    lines.append((sec if f["securities"] >= 1e4 else "無有價證券", "b"))
    return lines


SIZES = {"title": 96, "sub": 44, "big": 64, "h": 50, "b": 54, "note": 48, "gap": 16}  # 以 2000 px 寬為基準；手機全寬看 2000 px 圖約縮 5 倍，內文 54 px → 螢幕上約 11 px
COLORS = {"title": (255, 255, 255), "sub": (200, 200, 200), "big": (255, 214, 10), "h": (255, 196, 0),
          "b": (240, 240, 240), "note": (255, 140, 140), "gap": (0, 0, 0)}
FOOT = "中選會 115 年候選人財產申報 · 淨資產含不動產實價登錄參考估值（非鑑價）· 股票 2026-10-16 收盤價 · 看板財產透視 by 西格瑪機器人 Sigma Robotics · sigmabot.com.tw"
POSTER_AREA = 0.4  # 單一廣告框佔圖面 ≥ 40%（整張就是海報）→ 卡片改排在圖下方的面板，不蓋在人臉上


def _fonts(S, scale=1.0):
    fonts = {k: font(max(8, int(v * S * scale)), bold=k in ("title", "big")) for k, v in SIZES.items()}
    for k, f_ in fonts.items():
        try:
            f_.set_variation_by_name("Bold" if k in ("title", "big") else "Medium")
        except Exception:
            pass
    return fonts


def _card_metrics(d, lines, fonts, pad, S):
    cw = int(max(d.textlength(t, font=fonts[k]) for t, k in lines) + 2 * pad)
    lh = {k: int(fonts[k].size * 1.35) for k in fonts}
    ch = sum(lh[k] + (int(10 * S) if k in ("title", "h", "big") else 0) for _, k in lines) + 2 * pad
    return cw, ch, lh


def _draw_card(d, cx, cy, cw, ch, lines, fonts, lh, pad, S, bg=True):
    if bg:
        d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=int(18 * S), fill=(18, 23, 32, 215))
    ty = cy + pad
    for t, k in lines:
        if k in ("h", "big"):
            ty += int(10 * S)
        if t:
            d.text((cx + pad, ty), t, font=fonts[k], fill=COLORS[k] + (255,))
        ty += lh[k]


FOOT_H = 70


def _draw_footer(d, W, y, S):
    ff = font(int(26 * S))
    d.rectangle([0, y, W, y + int(FOOT_H * S)], fill=(0, 0, 0, 170))
    tw = d.textlength(FOOT, font=ff)
    if tw > W - int(40 * S):  # 太寬就拆兩行
        a, b_ = FOOT.split(" · 看板財產透視")
        for i, line in enumerate((a, "看板財產透視" + b_)):
            d.text(((W - d.textlength(line, font=ff)) / 2, y + int((6 + 32 * i) * S)), line, font=ff, fill=(230, 230, 230, 255))
    else:
        d.text(((W - tw) / 2, y + int(20 * S)), FOOT, font=ff, fill=(230, 230, 230, 255))


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def compose(im, results):
    """prepare() 後的圖＋facts_for_detections() 結果 → 疊圖（PIL Image，不存檔）。框位一律由 box_2d 依此圖尺寸換算。
    版面：單一廣告佔圖 ≥ 40% → 海報模式（整圖彩色、卡片在下方面板）；卡片疊得上圖就疊；疊不下（互相遮蓋）→ 圖上只放編號標籤，卡片排下方面板。"""
    W, H = im.size
    px = [box_px(r["detection"], W, H) for r in results]
    matched = [(r, b) for r, b in zip(results, px) if r.get("facts")]
    if any((b[2] - b[0]) * (b[3] - b[1]) >= POSTER_AREA * W * H for _, b in matched):
        return _panel(im.convert("RGBA"), matched, numbered=False)
    gray = ImageOps.grayscale(im).convert("RGB")
    for (x0, y0, x1, y1) in {tuple(b) for _, b in matched}:  # 只有對到候選人的廣告留彩色；多人同框只貼一次
        gray.paste(im.crop((x0, y0, x1, y1)), (x0, y0))
    canvas = gray.convert("RGBA")
    S = W / 2000
    fonts = _fonts(S)
    pad = int(22 * S)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    boxes = [tuple(b) for b in px]
    placed, plan = [], []
    for e, (x0, y0, x1, y1) in matched:
        lines = card_lines(e["facts"])
        cw, ch, lh = _card_metrics(probe, lines, fonts, pad, S)
        gap = int(30 * S)
        cands = [(x0, y1 + gap), (x1 - cw, y1 + gap), (x0, y0 - ch - gap), (x1 - cw, y0 - ch - gap), (x1 + gap, y0), (x0 - cw - gap, y0)]
        obstacles = boxes + placed

        def overlap(cx, cy):
            return sum(max(0, min(cx + cw, ox1) - max(cx, ox0)) * max(0, min(cy + ch, oy1) - max(cy, oy0)) for ox0, oy0, ox1, oy1 in obstacles)

        best = None
        for cx, cy in cands:
            cx, cy = min(max(cx, pad), W - cw - pad), min(max(cy, pad), H - ch - int((FOOT_H + 10) * S))
            ov = overlap(cx, cy)
            if best is None or ov < best[0]:
                best = (ov, cx, cy)
        ov, cx, cy = best
        while ov > 0 and cy + ch < H - int((FOOT_H + 10) * S):
            cy += int(20 * S); ov = overlap(cx, cy)
        placed.append((cx, cy, cx + cw, cy + ch))
        plan.append((ov, cx, cy, cw, ch, lines, lh, (x0, y0, x1, y1)))
    if cw > W - 2 * pad or any(p[0] > 0 for p in plan):  # 疊不下 → 編號標籤＋下方面板
        return _panel(canvas, matched, numbered=True)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for ov, cx, cy, cw, ch, lines, lh, (x0, y0, x1, y1) in plan:
        _draw_card(d, cx, cy, cw, ch, lines, fonts, lh, pad, S)
        below = cy > y1
        ax, ay = (x0 + x1) // 2, (y1 if below else y0)
        bx, by = cx + cw // 2, (cy if below else cy + ch)
        d.line([(ax, ay), (bx, by)], fill=(255, 255, 255, 200), width=int(4 * S))
        d.ellipse([ax - 8 * S, ay - 8 * S, ax + 8 * S, ay + 8 * S], fill=(255, 255, 255, 230))
    _draw_footer(d, W, H - int(FOOT_H * S), S)
    return Image.alpha_composite(canvas, overlay).convert("RGB")


def _panel(base, matched, numbered):
    """卡片排在圖下方的深色面板（每列最多 2 張，字才夠大）。numbered=True 時圖上每面廣告貼「① 姓名」標籤，卡片標題同號。"""
    W, H = base.size
    S = W / 2000
    if numbered:
        d0 = ImageDraw.Draw(base)
        tag_font = font(int(60 * S), bold=True)
        tags = []  # 已放的標籤矩形；同框多人或相鄰看板的標籤互撞就往下疊
        th, step = int(78 * S), int(84 * S)
        for i, (e, (x0, y0, x1, y1)) in enumerate(matched):
            label = f"{CIRCLED[i]} {e['facts']['cand']['name']}"
            tw = int(d0.textlength(label, font=tag_font) + 32 * S)
            tx, ty = min(x0, W - tw), y0
            while any(tx < ox1 and tx + tw > ox0 and ty < oy1 and ty + th > oy0 for ox0, oy0, ox1, oy1 in tags):
                ty += step
            tags.append((tx, ty, tx + tw, ty + th))
            d0.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=int(12 * S), fill=(18, 23, 32, 235))
            d0.text((tx + int(16 * S), ty + int(6 * S)), label, font=tag_font, fill=(255, 214, 10, 255))
    if W < 2000:  # 小圖放大到 2000 寬，讓卡片字夠大
        base = base.resize((2000, round(H * 2000 / W)), Image.LANCZOS)
        W, H = base.size; S = 1.0
    pad = int(22 * S)
    n = len(matched); cols = 1; rows = n  # 單欄：手機全寬才讀得到
    colw = (W - pad * (cols + 1)) // cols
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    cards = []
    for i, (e, _) in enumerate(matched):
        lines = card_lines(e["facts"])
        if numbered:
            lines[0] = (f"{CIRCLED[i]} {lines[0][0]}", "title")
        fonts = _fonts(S)
        cw, ch, lh = _card_metrics(probe, lines, fonts, pad, S)
        if cw > colw:  # 文字太寬就等比縮字
            fonts = _fonts(S, (colw - 2 * pad) / (cw - 2 * pad))
            cw, ch, lh = _card_metrics(probe, lines, fonts, pad, S)
        cards.append((lines, fonts, lh, ch))
    row_h = [max(cards[i][3] for i in range(r * cols, min(n, (r + 1) * cols))) for r in range(rows)]
    panel_h = sum(row_h) + pad * (rows + 1)
    foot_h = int(FOOT_H * S)
    canvas = Image.new("RGBA", (W, H + panel_h + foot_h), (18, 23, 32, 255))
    canvas.paste(base, (0, 0))
    d = ImageDraw.Draw(canvas)
    y = H + pad
    for r in range(rows):
        for c in range(cols):
            i = r * cols + c
            if i >= n:
                break
            lines, fonts, lh, ch = cards[i]
            x = pad + c * (colw + pad)
            d.rounded_rectangle([x, y, x + colw, y + row_h[r]], radius=int(18 * S), fill=(30, 36, 48, 255))
            _draw_card(d, x, y, colw, ch, lines, fonts, lh, pad, S, bg=False)
        y += row_h[r] + pad
    _draw_footer(d, W, H + panel_h, S)
    return canvas.convert("RGB")


def render(img_path, facts_json, out_path):
    data = json.load(open(facts_json))
    compose(prepare(img_path), data["results"]).save(out_path, quality=92)
    print("wrote", out_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("detect"); a.add_argument("image"); a.add_argument("--out", default="work")
    b = sp.add_parser("facts"); b.add_argument("detect_json")
    c = sp.add_parser("render"); c.add_argument("image"); c.add_argument("facts_json"); c.add_argument("--out", default=None)
    r_ = sp.add_parser("run"); r_.add_argument("image"); r_.add_argument("--out", default="work")
    args = ap.parse_args()
    if args.cmd == "detect":
        detect(Path(args.image), Path(args.out))
    elif args.cmd == "facts":
        facts(args.detect_json)
    elif args.cmd == "render":
        render(args.image, args.facts_json, args.out or str(Path(args.facts_json).with_name("overlay.jpg")))
    else:
        out = Path(args.out)
        detect(Path(args.image), out)
        facts(out / "detect.json")
        render(args.image, out / "facts.json", str(out / "overlay.jpg"))
