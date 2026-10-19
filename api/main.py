"""看板財產透視 API：街景照片 → Gemini 偵測候選人看板 → 115 年財產申報疊圖。

照片與結果只在記憶體處理，不落地；log 只記時間、偵測姓名、是否對到、耗時。
"""
import base64, io, logging, os, sys, threading, time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import billboard  # noqa: E402

MAX_BYTES = 10 * 1024 * 1024
PER_IP_PER_MIN = 5
DAILY_CAP = int(os.environ.get("DAILY_CAP", "500"))
TPE = timezone(timedelta(hours=8))
INDEX = Path(__file__).resolve().parent / "static/index.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("billboard")
logging.getLogger("uvicorn.access").disabled = True  # access log 會印 client IP，不記
for _name in ("httpx", "google_genai"):
    logging.getLogger(_name).setLevel(logging.WARNING)

app = FastAPI(title="2026 大選候選人財富雷達", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["https://sigma-snaken.github.io"], allow_methods=["GET", "POST"], allow_headers=["*"], expose_headers=["Retry-After"])


class ApiError(Exception):
    def __init__(self, status, msg, headers=None):
        self.status, self.msg, self.headers = status, msg, headers


@app.exception_handler(ApiError)
def _api_error(_, e: ApiError):
    return JSONResponse({"error": e.msg}, status_code=e.status, headers=e.headers)


@app.exception_handler(StarletteHTTPException)
def _http_error(_, e):
    msg = {404: "找不到這個網址", 405: "不支援這個請求方法"}.get(e.status_code, "請求失敗")
    return JSONResponse({"error": msg}, status_code=e.status_code)


@app.exception_handler(RequestValidationError)
def _validation_error(_, e):
    return JSONResponse({"error": "請以 multipart/form-data 上傳，欄位名稱為 photo"}, status_code=400)


@app.exception_handler(Exception)
def _unhandled(_, e):
    log.exception("unhandled error")
    return JSONResponse({"error": "伺服器內部錯誤，請稍後再試"}, status_code=500)


# ---------------------------------------------------------------- rate limit（單一 instance 記憶體內）
_lock = threading.Lock()
_hits = defaultdict(deque)
_day = {"date": None, "count": 0}


def client_ip(req: Request):
    """Cloud Run（K_SERVICE 有值）的前端會把真實來源 IP 附加在 X-Forwarded-For 最後，前面的段落客戶端可自填，不可信。"""
    xff = [x.strip() for x in req.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    if xff:
        return xff[-1] if os.environ.get("K_SERVICE") else xff[0]
    return req.client.host if req.client else "unknown"


def check_rate(ip):
    now = time.time()
    today = datetime.now(TPE)
    with _lock:
        if _day["date"] != today.date():
            _day.update(date=today.date(), count=0)
        if _day["count"] >= DAILY_CAP:
            midnight = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            raise ApiError(429, "今日全站分析次數已達上限，請明天再試", {"Retry-After": str(int((midnight - today).total_seconds()) + 1)})
        q = _hits[ip]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= PER_IP_PER_MIN:
            raise ApiError(429, f"每分鐘最多分析 {PER_IP_PER_MIN} 張，請稍後再試", {"Retry-After": str(int(60 - (now - q[0])) + 1)})
        q.append(now)
        _day["count"] += 1
        for k in [k for k, v in _hits.items() if not v or now - v[-1] >= 60]:
            del _hits[k]


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(INDEX, media_type="text/html; charset=utf-8")


@app.post("/api/report")
def report(request: Request, hours: int = 24, quiet: int = 0):
    """Cloud Scheduler 觸發：統計 log 送 Telegram。quiet=1 時該時段沒人用就不送。"""
    token = os.environ.get("BILLBOARD_REPORT_TOKEN")
    if not token or request.headers.get("X-Report-Token") != token:
        raise ApiError(404, "找不到這個網址")
    from api import report as rpt
    text, recent = rpt.build(max(1, min(hours, 24 * 30)))
    if quiet and not (recent["pages"] or recent["reqs"]):
        return {"sent": False}
    rpt.send_telegram(text)
    return {"sent": True}


@app.get("/healthz")
def healthz():
    return {"ok": True}


def candidate_summary(f):
    c = f["cand"]
    keep = ("county", "town", "towns", "home", "units", "land_rows", "land_m2", "est")
    return {"name": c["name"], "elect_type": c["elect_type"], "district": c["district"], "elect_towns": f["elect_towns"],
            "net_with_re": f["net_with_re"], "re_est": f["re_est"], "mortgage": f["mortgage"],
            "securities": f["securities"], "stock_mkt": f["stock_mkt"], "home": f["home"],
            "realestate": [{k: g[k] for k in keep} for g in f["realestate"]]}


@app.post("/api/analyze")
def analyze(request: Request, photo: UploadFile = File(...)):
    t0 = time.time()
    check_rate(client_ip(request))  # 先限流再碰檔案，別替攻擊者解碼大圖
    if (photo.content_type or "").split("/")[0] != "image":
        raise ApiError(415, "請上傳 JPEG／PNG 等照片")
    data = photo.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ApiError(413, "照片超過 10 MB，請縮小後再上傳")
    try:
        im = billboard.prepare(data)
    except Exception:
        raise ApiError(415, "無法讀取這個檔案，請上傳 JPEG／PNG 等照片")
    del data
    try:
        detections = billboard.detect_image(im)
    except billboard.DetectTimeout:
        log.warning("gemini timeout after retry")
        raise ApiError(504, "辨識逾時，請再試一次")
    except Exception as e:
        log.warning("gemini failed: %s code=%s status=%s msg=%s", type(e).__name__, getattr(e, "code", None), getattr(e, "status", None), str(getattr(e, "message", e))[:300])
        raise ApiError(502, "AI 判讀失敗，請稍後再試")
    results = billboard.facts_for_detections(detections)
    buf = io.BytesIO()
    billboard.compose(im, results).save(buf, "JPEG", quality=88)
    elapsed = int((time.time() - t0) * 1000)
    log.info("analyze %s elapsed_ms=%d", [(r["person"], bool(r.get("facts"))) for r in results], elapsed)
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
        "candidates": [candidate_summary(r["facts"]) for r in results if r.get("facts")],
        "detections": [{"name": r["person"], "board": r["detection"]["name"], "office": r["detection"]["office"], "confidence": r["detection"]["confidence"],
                        "matched": bool(r.get("facts")), "how": r["how"]} for r in results],
        "elapsed_ms": elapsed,
    }
