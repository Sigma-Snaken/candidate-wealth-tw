"""實價登錄 (MOI bulk open data) -> per-section / road / town unit-price quantiles for valuing declared land and buildings.

  python pipeline/lvr.py fetch --year 115       download missing season zips to data/lvr_raw/
  python pipeline/lvr.py aggregate --year 115   write data/prices/<lvr_file>

Only aggregates leave this script: keys stop at 段/小段, road name (no number) or 鄉鎮市區; 地號/門牌 are never written.
"""
import argparse, csv, io, json, os, re, statistics, sys, unicodedata, urllib.request, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

LVR_RAW = os.path.join(paths.DATA, 'lvr_raw')
URL = 'https://plvr.land.moi.gov.tw/DownloadSeason?season={}&type=zip&fileName=lvr_landcsv.zip'
EXCLUDE = ['親友', '特殊關係', '協議價購', '政府機關', '標讓售', '標售', '急買急賣', '債權債務', '債務抵償', '瑕疵', '畸零地', '公共設施保留地',
           '含未來興建房屋成本', '合建', '包含其他買賣登記案件', '民情風俗', '水利地', '容積代金', '地上權', '重建或重劃', '都更']
EXCLUDE_LAND = ['土地及建物分件登記', '未登記建物', '農作物', '農業設施']
LAND_RANGE, HOUSE_RANGE = (10, 10_000_000), (1_000, 10_000_000)
BIG = 1000                                                    # m²: land sales transferring this much area also go into the SL/S0L/TL keys
RULES = ('土地：交易標的＝土地，單價＝總價÷土地移轉總面積，所有地號同一段小段才歸入該段；'
         f'土地移轉總面積達 {BIG:,} m² 者另計入大面積鍵 SL／S0L／TL；'
         '房地：交易標的為房地，單價＝(總價−車位總價)÷(建物移轉總面積−車位移轉總面積)，車位未單獨計價（車位總價 0）者以 總價÷建物移轉總面積；'
         '段取其土地筆的段小段（不一致取第一筆）、路名取門牌去除門牌號；段名去括號、異體字（塩→鹽、双→雙）與申報資料同樣正規化；'
         '土地以成交的地號組合（段＋地號，只在程式內比對、不輸出）辨識，同一組地號在同一鍵內只計一筆（多次成交取單價中位數）；'
         '房地同一鍵內同交易日、同單價（取整）之多筆視為同一批分割出售，只計一次；'
         f'排除備註含 {"、".join(EXCLUDE)} 之交易，土地另排除 {"、".join(EXCLUDE_LAND)}；'
         f'單價超出 土地 {LAND_RANGE[0]}–{LAND_RANGE[1]:,} 或 房地 {HOUSE_RANGE[0]:,}–{HOUSE_RANGE[1]:,} 元/m² 亦排除。僅使用不動產買賣（a 檔），不含預售屋與租賃。')
CN = '零一二三四五六七八九'
VARIANT = str.maketrans('塩双', '鹽雙')

def cn(n):
    n = int(n)
    return CN[n] if n < 10 else ('' if n < 20 else CN[n // 10]) + '十' + (CN[n % 10] if n % 10 else '') if n < 100 else str(n)

def clean(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s or '')).replace('台', '臺').replace('巿', '市').translate(VARIANT)

def norm_sec(s):
    """Section name as both sides use it: no parentheses, Arabic -> Chinese numerals before 段/小段, blank or 0 小段 dropped."""
    s = re.sub(r'[(（].*?[)）]', '', clean(s))
    s = re.sub(r'(\d+)(?=小段|段)', lambda m: cn(m.group(1)), s)
    s = re.sub(r'(段)(?:零)?小段', r'\1', s)                 # 玉泉段 小段 / 社后段小段 / 新化段0小段
    return re.sub(r'(建號|地號|\d.*)$', '', s)

ROAD = re.compile(r'^(\D+?(?:路|街|大道)(?:[一二三四五六七八九十]+段)?)')

def road(rest):
    """Road name (no number) at the start of an address remainder, allowing a leading 村/里 (+鄰) prefix."""
    rest = re.sub(r'(\d+)段', lambda m: cn(m.group(1)) + '段', clean(rest))
    m = re.match(r'^\D{1,4}?[村里](?:\d+鄰)?(.+)$', rest)
    for s in ([m.group(1)] if m else []) + [rest]:
        r = ROAD.match(s)
        if r and not re.search(r'[巷弄號鄰之]', r.group(1)): return r.group(1)
    return None

def sec0(sec):
    return sec[:sec.index('段') + 1] if '段' in sec else None

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

def quart(v):
    v = sorted(v)
    q = statistics.quantiles(v, n=4, method='inclusive') if len(v) > 1 else [v[0]] * 3
    return [len(v)] + [round(x) for x in q]

def rows(z, name, english=True):
    r = csv.reader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'))
    head = next(r)
    if english: next(r, None)
    for row in r: yield dict(zip(head, row))

def fetch(year):
    os.makedirs(LVR_RAW, exist_ok=True)
    for s in paths.YEARS[year]['lvr_seasons']:
        f = os.path.join(LVR_RAW, f'{s}.zip')
        if os.path.exists(f): print('have', s); continue
        req = urllib.request.Request(URL.format(s), headers={'User-Agent': 'Mozilla/5.0'})
        data = urllib.request.urlopen(req, timeout=300).read()
        zipfile.ZipFile(io.BytesIO(data)).testzip()
        open(f, 'wb').write(data); print('got', s, len(data))

def aggregate(year):
    cfg = paths.YEARS[year]; land, house, cnt, built, dates = {}, {}, {}, {}, []
    y, m, d = cfg['price_date'].split('-'); lo, hi = f"{int(cfg['lvr_seasons'][0][:3]) - 1:03d}0101", f'{int(y) - 1911:03d}{m}{d}'
    bump = lambda k: cnt.__setitem__(k, cnt.get(k, 0) + 1)
    add = lambda d, keys, i, u: [d.setdefault(k, {}).setdefault(i, []).append(u) for k in keys]   # one observation per id: parcel set (land) or (date, rounded unit)
    for s in cfg['lvr_seasons']:
        z = zipfile.ZipFile(os.path.join(LVR_RAW, f'{s}.zip'))
        built[s] = re.search(r'<lvr_time>(.*?)</lvr_time>', z.read('build_time.xml').decode('utf-8'), re.S).group(1).strip()
        for m in rows(z, 'manifest.csv', False):
            if not m['name'].endswith('_lvr_land_a.csv'): continue
            city = clean(re.match(r'(.+)不動產買賣$', m['description']).group(1))
            secs = {}                                                       # 編號 -> [(段, 地號)]; 地號 stays in memory
            for r in rows(z, m['name'].replace('_a.csv', '_a_land.csv')):
                secs.setdefault(r['編號'], []).append((norm_sec(r['土地位置']), r['地號']))
            for r in rows(z, m['name']):
                kind = r['交易標的']; is_land = kind == '土地'
                if not (is_land or kind.startswith('房地')): bump('skip_other_target'); continue
                note = r['備註'] or ''
                why = next((k for k in EXCLUDE + (EXCLUDE_LAND if is_land else []) if k in note), None)
                if why: bump('excl_' + why); continue
                if not lo <= r['交易年月日'] <= hi or len(r['交易年月日']) != 7: bump('excl_tx_date_outside_window'); continue
                town = clean(r['鄉鎮市區'])
                if not town: bump('skip_no_town'); continue
                ss = [x for x, _ in secs.get(r['編號'], []) if '段' in x]
                if is_land:
                    tot, a = num(r['總價元']), num(r['土地移轉總面積平方公尺'])
                    if tot <= 0 or a <= 0: bump('skip_no_price_or_area'); continue
                    unit, rng = tot / a, LAND_RANGE
                    sec = ss[0] if ss and len(set(ss)) == 1 else None
                    big = a >= BIG
                else:
                    pa, pp = num(r['車位移轉總面積平方公尺']), num(r['車位總價元'])
                    if pa > 0 and pp == 0: bump('note_parking_area_without_price'); pa = 0      # parking priced inside 總價
                    tot, a = num(r['總價元']) - pp, num(r['建物移轉總面積平方公尺']) - pa
                    if tot <= 0 or a <= 0: bump('skip_no_price_or_area'); continue
                    unit, rng = tot / a, HOUSE_RANGE
                    sec = ss[0] if ss else None
                if not rng[0] <= unit <= rng[1]: bump('excl_unit_out_of_range'); continue
                keys = [f'T|{city}|{town}']
                if sec: keys += [f'S|{city}|{town}|{sec}', f'S0|{city}|{town}|{sec0(sec)}']
                if is_land and big: keys += [k.replace('|', 'L|', 1) for k in keys]
                if not is_land:
                    addr = clean(r['土地位置建物門牌'])
                    if addr.startswith(city): addr = addr[len(city):]
                    addr = re.sub('^(?:' + re.escape(town[:-1]) + '[市鄉鎮區])+', '', addr)    # town is sometimes written twice
                    rd = road(addr)
                    if rd: keys.append(f'R|{city}|{town}|{rd}')
                pid = tuple(sorted({x for x in secs.get(r['編號'], []) if x[1]})) if is_land else None
                add(land if is_land else house, keys, pid or (r['交易年月日'], round(unit)), round(unit))
                bump('kept_land' if is_land else 'kept_house'); dates.append(r['交易年月日'])
    for kind, d in (('land', land), ('house', house)): cnt[f'batch_dup_{kind}'] = cnt[f'kept_{kind}'] - sum(len(v) for k, v in d.items() if k[:2] == 'T|')
    land, house = ({k: quart([statistics.median(u) for u in v.values()]) for k, v in sorted(d.items())} for d in (land, house))
    bad = [k for k in list(land) + list(house) if re.search(r'\d', k.split('|', 1)[1]) or k[0] == 'R' and re.search(r'號|巷|弄|鄰|之', k)]
    assert not bad, bad[:20]
    dates.sort()
    meta = dict(seasons=cfg['lvr_seasons'], source=URL.format('<season>'), build_time=built, tx_date_min=dates[0], tx_date_max=dates[-1],
                counts=dict(sorted(cnt.items())), rules=RULES + f'交易日期限 {lo}–{hi}（排除遲延登記之舊案與誤植日期）。', columns='[n, p25, median, p75] 元/m²')
    out = os.path.join(paths.PRICES, cfg['lvr_file'])
    with open(out, 'w', encoding='utf-8') as f: json.dump(dict(meta=meta, land=land, house=house), f, ensure_ascii=False, separators=(',', ':'))
    print(out, os.path.getsize(out), 'land keys', len(land), 'house keys', len(house)); print(json.dumps(meta, ensure_ascii=False, indent=1))

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['fetch', 'aggregate']); ap.add_argument('--year', required=True, choices=list(paths.YEARS))
    a = ap.parse_args()
    fetch(a.year) if a.cmd == 'fetch' else aggregate(a.year)
