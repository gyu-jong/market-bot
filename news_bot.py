#!/usr/bin/env python3
"""news_bot.py
경제 / 재테크 / 주식·ETF / 건강(심혈관) / AI 뉴스를 모아
'제목(링크) + 5줄 요약'으로 텔레그램 발송 + HTML 아카이브(docs/news/) 저장.

사용:  python news_bot.py 0700    (또는 1700)
환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY
         (선택) NEWS_MODEL (기본 claude-haiku-5-5)
"""
import calendar
import datetime as dt
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import requests

KST = ZoneInfo("Asia/Seoul")
UA = {"User-Agent": "Mozilla/5.0 (news-bot)"}
MODEL = os.environ.get("NEWS_MODEL", "claude-haiku-5-5")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

TG_SUMMARY_TOP = 5                # 분야별 상위 N건만 텔레그램에 요약 포함, 나머지는 제목+링크만 (0이면 전부 제목만)
MAX_AGE_HOURS = 30                # 이보다 오래된 기사는 제외
BATCH = 8                         # 요약 API 1회당 기사 수
OUT_DIR = "docs/news"


def gnews(q):
    """구글뉴스 검색 RSS (최근 1일)"""
    return f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=ko&gl=KR&ceid=KR:ko"


CARDIO_KW = ["심혈관", "심근경색", "협심증", "부정맥", "심방세동", "심부전", "고혈압", "혈압",
             "콜레스테롤", "스타틴", "동맥경화", "뇌졸중", "혈전", "심장", "LDL", "중성지방"]

# 피드 URL은 바뀔 수 있으니 실행 로그의 'FEED FAIL' 줄을 보고 교체하세요.
CATEGORIES = [
    {"name": "경제", "n": 20, "boost": [],
     "feeds": ["https://www.yna.co.kr/rss/economy.xml",
               "https://www.hankyung.com/feed/economy",
               gnews("경제 금리 환율 물가")]},
    {"name": "재테크", "n": 20, "boost": [],
     "feeds": [gnews("재테크 연금 ISA 절세"),
               gnews("예금 적금 금리 재테크"),
               "https://www.hankyung.com/feed/realestate"]},
    {"name": "주식/ETF", "n": 30, "boost": [],
     "feeds": ["https://www.yna.co.kr/rss/market.xml",
               "https://www.hankyung.com/feed/finance",
               gnews("코스피 코스닥 증시"),
               gnews("ETF 나스닥 S&P500 미국 증시")]},
    {"name": "건강(심혈관)", "n": 20, "boost": CARDIO_KW,
     "feeds": [gnews("심혈관 심근경색 부정맥 심부전"),
               gnews("고혈압 콜레스테롤 스타틴 혈관 건강"),
               "https://www.yna.co.kr/rss/health.xml"]},
    {"name": "AI", "n": 30, "boost": [],
     "feeds": [gnews("인공지능 AI 생성형"),
               gnews("OpenAI Anthropic 엔비디아 AI 반도체"),
               "https://www.hankyung.com/feed/it"]},
]


# ---------- 수집 ----------
def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def norm_title(t):
    t = re.sub(r"\s-\s[^-]+$", "", t)           # 구글뉴스 ' - 매체명' 제거
    return re.sub(r"[^0-9a-z가-힣]", "", t.lower())[:26]


def read_feed(url):
    try:
        r = requests.get(url, timeout=15, headers=UA)
        r.raise_for_status()
        d = feedparser.parse(r.content)
        out = []
        for e in d.entries:
            if e.get("published_parsed"):
                ts = calendar.timegm(e.published_parsed)
            else:
                ts = time.time()
            src = (e.get("source") or {}).get("title") or d.feed.get("title", "")
            out.append({"title": strip_tags(e.get("title", "")), "link": e.get("link", ""),
                        "ts": ts, "src": src, "desc": strip_tags(e.get("summary", ""))})
        if not out:
            print("FEED EMPTY", url)
        return out
    except Exception as ex:
        print("FEED FAIL", url, ex)
        return []


def collect(cat, seen):
    with ThreadPoolExecutor(4) as ex:
        lists = list(ex.map(read_feed, cat["feeds"]))
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    cands, local = [], set()
    for it in (x for l in lists for x in l):
        k = norm_title(it["title"])
        if not k or k in seen or k in local or it["ts"] < cutoff:
            continue
        local.add(k)
        it["key"] = k
        text = it["title"] + " " + it["desc"]
        it["score"] = sum(kw in text for kw in cat["boost"])
        cands.append(it)
    cands.sort(key=lambda x: (-x["score"], -x["ts"]))
    picked = cands[: cat["n"]]
    seen.update(x["key"] for x in picked)
    if len(picked) < cat["n"]:
        print(f"WARN {cat['name']}: {len(picked)}/{cat['n']}건만 확보")
    return picked


# ---------- 본문 / 요약 ----------
def body_of(it):
    if "news.google.com" in it["link"]:
        return ""
    try:
        import trafilatura
        r = requests.get(it["link"], timeout=10, headers=UA)
        return (trafilatura.extract(r.text) or "")[:3500]
    except Exception:
        return ""


def summarize_batch(items):
    fallback = lambda it: [(it["desc"] or it["title"])[:120]]
    try:
        import anthropic
        client = anthropic.Anthropic()
        payload = [{"id": i, "title": it["title"],
                    "text": (it["body"] or it["desc"])[:3000]} for i, it in enumerate(items)]
        prompt = (
            "아래 뉴스 각각을 한국어로 정확히 5줄로 요약하세요. 줄당 60자 안팎, 사실·수치 중심.\n"
            "원문 문장을 그대로 베끼지 말고 자신의 말로 쓰세요. text에 없는 내용은 추측하지 말고, "
            "정보가 부족하면 5줄을 채우려 하지 말고 줄 수를 줄이세요.\n"
            'JSON 배열만 출력: [{"id":0,"lines":["...","..."]}]\n\n'
            + json.dumps(payload, ensure_ascii=False))
        resp = client.messages.create(model=MODEL, max_tokens=4000,
                                      messages=[{"role": "user", "content": prompt}])
        txt = re.sub(r"^```(?:json)?|```$", "", resp.content[0].text.strip(), flags=re.M).strip()
        data = {d["id"]: d["lines"] for d in json.loads(txt)}
        for i, it in enumerate(items):
            it["lines"] = [l.strip() for l in data.get(i, fallback(it))][:5]
    except Exception as ex:
        print("SUMMARY FAIL", ex)
        for it in items:
            it["lines"] = fallback(it)


def enrich_and_summarize(items):
    with ThreadPoolExecutor(8) as ex:
        for it, b in zip(items, ex.map(body_of, items)):
            it["body"] = b
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(summarize_batch, batches))


# ---------- 출력 ----------
def fmt(i, it, with_summary=True):
    s = f"{i}. {it['title']}\n{it['link']}"
    if with_summary:
        s += "\n" + "\n".join("· " + l for l in it["lines"])
    return s


_sent = [0]


def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        print("텔레그램 설정 없음 - 발송 생략")
        return
    silent = _sent[0] > 0          # 첫 메시지만 알림, 나머지는 무음
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": text,
                            "disable_web_page_preview": "true",
                            "disable_notification": "true" if silent else "false"},
                      timeout=20)
    if not r.ok:
        print("TG FAIL", r.status_code, r.text[:200])
    _sent[0] += 1
    time.sleep(1.1)


def pages_url():
    base = os.environ.get("NEWS_PAGES_URL")
    if base:
        return base
    repo = os.environ.get("GITHUB_REPOSITORY", "")      # Actions에서 자동 설정 (owner/repo)
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/news/latest.html"
    return "docs/news/latest.html"


def send_category(cat, items, label):
    blocks = [fmt(i, it, i <= TG_SUMMARY_TOP) for i, it in enumerate(items, 1)]
    chunks, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) + 2 > 3700:
            chunks.append(cur)
            cur = ""
        cur += b + "\n\n"
    if cur:
        chunks.append(cur)
    for n, c in enumerate(chunks, 1):
        head = f"📰 [{cat['name']}] {label} ({len(items)}/{cat['n']}건) {n}/{len(chunks)}\n\n"
        tg_send(head + c.strip())


def write_html(result, label, stamp):
    os.makedirs(OUT_DIR, exist_ok=True)
    parts = [f"<h1>뉴스 {html.escape(label)}</h1>"]
    for cat, items in result:
        parts.append(f"<h2>{html.escape(cat['name'])} ({len(items)})</h2>")
        for i, it in enumerate(items, 1):
            lines = "".join(f"<li>{html.escape(l)}</li>" for l in it["lines"])
            parts.append(
                f"<div class='a'><b>{i}. <a href='{html.escape(it['link'])}'>{html.escape(it['title'])}</a></b>"
                f"<small> {html.escape(it['src'])}</small><ul>{lines}</ul></div>")
    page = ("<!doctype html><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<style>body{font-family:system-ui;max-width:760px;margin:auto;padding:12px;line-height:1.5}"
            ".a{margin:14px 0;padding-bottom:8px;border-bottom:1px solid #8884}small{color:#888}"
            "ul{margin:6px 0 0 18px;padding:0}</style>" + "".join(parts))
    for name in ("latest.html", f"{stamp}.html"):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            f.write(page)


def wait_until(hhmm):
    """목표 시각(KST)까지 대기 (최대 45분). 미리 호출해 두고 정시에 보내는 용도"""
    try:
        h, m = int(hhmm[:2]), int(hhmm[2:4])
    except Exception:
        return
    now = dt.datetime.now(KST)
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    wait = (t - now).total_seconds()
    if 0 < wait <= 45 * 60:
        print(f"{int(wait)}초 대기 후 발송")
        time.sleep(wait)


def main():
    slot = sys.argv[1] if len(sys.argv) > 1 else ""
    now = dt.datetime.now(KST)
    label = f"{now:%m/%d} {slot[:2]}:{slot[2:4]}" if len(slot) >= 4 else f"{now:%m/%d %H:%M}"
    stamp = f"{now:%Y%m%d}-{slot or now.strftime('%H%M')}"

    seen, result = set(), []
    for cat in CATEGORIES:
        items = collect(cat, seen)
        enrich_and_summarize(items)
        result.append((cat, items))
        print(f"{cat['name']}: {len(items)}건 완료")

    write_html(result, label, stamp)
    wait_until(slot)
    for cat, items in result:
        send_category(cat, items, label)
    tg_send(f"📄 전체 요약 보기 ({label})\n{pages_url()}")


if __name__ == "__main__":
    main()
