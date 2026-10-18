"""使用量摘要：讀 Cloud Run log（不是照片）統計看板站流量，送 Telegram。

Cloud Scheduler 每天／每小時打 POST /api/report；log 只有時間、狀態碼、UA、referer 與偵測到的姓名。
"""
import os, re
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from google.cloud import logging as gcl

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "sigma-snaken-dev")
SERVICE = os.environ.get("K_SERVICE", "candidate-billboard")
LAUNCH = datetime(2026, 10, 18, 13, 5, tzinfo=timezone.utc)  # Threads 貼文基準點（首筆 Threads referer 13:09:57Z）
COST_PER_PHOTO = 0.007  # USD，ER 2 實測均值含偶發重試
TPE = timezone(timedelta(hours=8))
_client = None


def _entries(since, filt):
    global _client
    if _client is None:
        _client = gcl.Client(project=PROJECT)
    base = (f'resource.type="cloud_run_revision" AND resource.labels.service_name="{SERVICE}" '
            f'AND timestamp>="{since.strftime("%Y-%m-%dT%H:%M:%SZ")}"')
    return list(_client.list_entries(filter_=f"{base} AND {filt}", page_size=1000, max_results=5000))


def _agent(u):
    u = u or ""
    if re.search(r"facebookexternalhit|Threads|bot|crawler|spider|preview|Expanse", u, re.I):
        return "爬蟲"
    if "iPhone" in u or "Android" in u or "Mobile" in u:
        return "手機"
    if "HeadlessChrome" in u or "Playwright" in u or u.startswith("curl") or "python" in u.lower():
        return "測試"
    return "桌機" if "Mozilla" in u else "其他"


def stats(since):
    pages = [e.http_request or {} for e in _entries(since, r'httpRequest.requestUrl=~"run\.app/(\?.*)?$"')]  # 只算首頁，不含 favicon／api
    reqs = [e.http_request or {} for e in _entries(since, 'httpRequest.requestUrl:"/api/analyze"')]
    lines = [e.payload for e in _entries(since, 'textPayload:"analyze ["') if isinstance(e.payload, str)]
    names, empty, unmatched = Counter(), 0, Counter()
    for p in lines:
        pairs = re.findall(r"\('([^']*)', (True|False)\)", p)
        empty += not pairs
        for n, ok in pairs:
            (names if ok == "True" else unmatched)[n] += 1
    refer = Counter()
    for p in pages:
        r = (p.get("referer") or "").split("?")[0]
        refer["Threads" if "threads" in r else "直接／其他" if not r or "run.app" in r else r[:40]] += 1
    return {
        "pages": len(pages), "page_agent": Counter(_agent(p.get("userAgent")) for p in pages), "refer": refer,
        "reqs": len(reqs), "status": Counter(str(p.get("status")) for p in reqs), "req_agent": Counter(_agent(p.get("userAgent")) for p in reqs),
        "photos": len(lines), "empty": empty, "names": names, "unmatched": unmatched,
    }


def _fmt(s):
    bad = sum(v for k, v in s["status"].items() if k.startswith(("4", "5")))
    out = [f"首頁開啟 {s['pages']} 次（{_join(s['page_agent'])}；來源 {_join(s['refer'])}）",
           f"照片判讀 {s['photos']} 張（{_join(s['req_agent'])}）：沒框到 {s['empty']}、對不到 {sum(s['unmatched'].values())}"]
    if bad:
        out.append(f"錯誤回應 {bad} 次：{_join(s['status'])}")
    if s["names"]:
        top = "、".join(f"{n}×{c}" if c > 1 else n for n, c in s["names"].most_common(12))
        out.append(f"對到：{top}" + ("…" if len(s["names"]) > 12 else ""))
    if s["unmatched"]:
        out.append("對不到：" + "、".join(list(s["unmatched"])[:6]))
    out.append(f"Gemini 約 ${s['photos'] * COST_PER_PHOTO:.2f}")
    return "\n".join(out)


def _join(c):
    return "、".join(f"{k} {v}" for k, v in c.most_common()) or "0"


def build(hours):
    now = datetime.now(timezone.utc)
    recent = stats(now - timedelta(hours=hours))
    total = stats(LAUNCH)
    head = f"📊 候選人財富雷達 {now.astimezone(TPE):%m/%d %H:%M}"
    text = f"{head}\n\n最近 {hours} 小時\n{_fmt(recent)}\n\n貼文以來累計\n{_fmt(total)}"
    return text, recent


def send_telegram(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 未設定")
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=20)
    r.raise_for_status()


if __name__ == "__main__":  # 本機試跑：python api/report.py [hours]（用 ADC，不送 Telegram）
    import sys
    print(build(int(sys.argv[1]) if len(sys.argv) > 1 else 24)[0])
