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
SEEN_FILE = os.path.join(OUT_DIR, "seen.json")   # 이미 보낸 기사 이력 (워크플로가 저장소에 커밋)
SEEN_KEEP_HOURS = 96                              # 이력 보관 기간


def load_seen():
    """{기사키: 발송시각} - 보관 기간이 지난 항목은 버림"""
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    cut = time.time() - SEEN_KEEP_HOURS * 3600
    return {k: v for k, v in d.items() if isinstance(v, (int, float)) and v >= cut}


def save_seen(d):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def gnews(q, days=1):
    """구글뉴스 검색 RSS (최근 N일, 한국어)"""
    return f"https://news.google.com/rss/search?q={quote(f'{q} when:{days}d')}&hl=ko&gl=KR&ceid=KR:ko"


def gnews_en(q, days=2):
    """구글뉴스 검색 RSS (최근 N일, 영어) - AI 분야 보강용"""
    return f"https://news.google.com/rss/search?q={quote(f'{q} when:{days}d')}&hl=en-US&gl=US&ceid=US:en"


def hit(text, kws):
    """포함된 키워드 개수 (영문 키워드는 단어 경계 기준, 한글은 부분 일치)"""
    t = text.lower()
    n = 0
    for k in kws:
        k = k.lower()
        if k.isascii():
            if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"s?(?![a-z0-9])", t):
                n += 1
        elif k in t:
            n += 1
    return n


# ---- 건강 분류 (우선순위 낮을수록 먼저 채움, 해당 없으면 제외)
#   0 = 질환·검사수치·노화 주제 + 음식/운동/영양 내용 함께
#   1 = 질환·검사수치·노화 주제 기사
#   2 = 음식/운동/영양 기사 중 건강 관련 표현이 있는 것
#   3 = 체중관리·다이어트·수면 (위가 부족할 때 채움)
H_TOPIC = ["혈당", "당화혈색소", "당뇨", "고혈압", "혈압", "고지혈증", "이상지질혈증", "LDL",
           "콜레스테롤", "중성지방", "혈관", "심혈관", "노화", "항노화", "저속노화", "췌장",
           # '간'은 '한미 간', '기간' 등과 섞이므로 간 관련 합성어로만 찾음
           "지방간", "간암", "간염", "간경변", "간경화", "간수치", "간 수치", "간기능", "간 기능",
           "간 건강", "간질환", "간 질환", "간 손상", "간 지방"]
# '암'도 '암호', '암모니아' 등과 섞이므로 '대장암', '암 환자'처럼 독립된 말일 때만 인정
CANCER_RE = re.compile(
    r"(?:[가-힣]{1,5}암|(?<![가-힣])암)(?:에서|으로|이|은|는|을|를|의|과|와|도|에|로|가)?(?![가-힣])")
H_LIFE = ["음식", "식단", "식품", "식이", "식습관", "저염", "지중해식", "섬유질", "오메가3",
          "포화지방", "단백질", "탄수화물", "운동", "걷기", "근력", "유산소", "생활습관",
          "생활 습관", "금연", "금주", "스트레스"]
H_MARK = ["건강", "질환", "질병", "예방", "수치", "위험", "의학", "의사", "연구", "전문가", "교수"]
H_WEIGHT = ["체중", "다이어트", "비만", "감량", "체지방", "수면", "불면", "숙면"]


def classify_health(text):
    topic = hit(text, H_TOPIC) > 0 or bool(CANCER_RE.search(text))
    life = hit(text, H_LIFE) > 0
    if topic and life:
        return 0
    if topic:
        return 1
    if life and hit(text, H_MARK):
        return 2
    if hit(text, H_WEIGHT):
        return 3
    return None


# ---- AI 분류: AI 관련 기사 중 API·연결(에이전트/MCP/자동화 등)은 0순위,
#               서비스 변화·활용법은 1순위, 그 외는 제외
A_MARK = ["AI", "인공지능", "LLM", "생성형", "챗GPT", "ChatGPT", "GPT", "제미나이", "Gemini",
          "클로드", "Claude", "코파일럿", "Copilot", "오픈AI", "OpenAI", "앤트로픽", "Anthropic",
          "딥시크", "DeepSeek", "Llama", "Mistral"]
A_LINK = ["API", "SDK", "MCP", "에이전트", "agent", "워크플로", "workflow", "자동화", "automation",
          "연동", "연결", "오케스트레이션", "멀티모델", "RAG", "플러그인", "커넥터", "n8n",
          "Zapier", "LangChain", "엔드포인트", "integration"]
A_SERVICE = ["출시", "업데이트", "서비스", "요금", "가격", "무료", "정책", "개편", "신규", "새 모델",
             "버전", "launch", "release", "pricing", "update", "announce"]
A_USE = ["활용", "사용법", "사용 방법", "가이드", "튜토리얼", "프롬프트", "노하우", "실무",
         "tutorial", "guide", "how to"]


def classify_ai(text):
    if not hit(text, A_MARK):
        return None
    if hit(text, A_LINK):
        return 0
    if hit(text, A_SERVICE) or hit(text, A_USE):
        return 1
    return None

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
    {"name": "건강", "n": 50, "max_age": 48, "classify": classify_health,
     "feeds": [gnews("혈당 당화혈색소 당뇨 식단 운동", 2),
               gnews("고혈압 고지혈증 이상지질혈증 관리", 2),
               gnews("LDL 콜레스테롤 포화지방 식단", 2),
               gnews("혈관 심혈관 건강 음식 운동", 2),
               gnews("노화 저속노화 단백질 근력 운동", 2),
               gnews("탄수화물 단백질 포화지방 건강", 2),
               gnews("암 예방 음식 운동 생활습관", 2),
               gnews("췌장 지방간 간 건강 식단", 2),
               gnews("체중관리 다이어트 수면 관리", 2),
               "https://www.yna.co.kr/rss/health.xml"]},
    {"name": "AI", "n": 30, "max_age": 48, "classify": classify_ai,
     "feeds": [gnews("AI API 출시 요금 업데이트", 2),
               gnews("생성형 AI 활용법 업무 자동화", 2),
               gnews("AI 에이전트 MCP 연동 워크플로", 2),
               gnews("OpenAI Anthropic Gemini 새 모델 서비스", 2),
               gnews_en("AI API pricing new model release"),
               gnews_en("AI agents MCP workflow integration"),
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
    cutoff = time.time() - cat.get("max_age", MAX_AGE_HOURS) * 3600
    clf = cat.get("classify")
    cands, local = [], set()
    for it in (x for l in lists for x in l):
        k = norm_title(it["title"])
        if not k or k in seen or k in local or it["ts"] < cutoff:
            continue
        tier = clf(it["title"] + " " + it["desc"]) if clf else 0
        if tier is None:          # 분야 조건에 맞지 않는 기사는 제외
            continue
        local.add(k)
        it["key"] = k
        it["tier"] = tier
        cands.append(it)
    cands.sort(key=lambda x: (x["tier"], -x["ts"]))   # 순위 우선, 같으면 최신순
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
    # 텔레그램 HTML 모드: 제목 자체가 링크, 주소는 화면에 노출하지 않음
    s = f"{i}. <a href=\"{html.escape(it['link'], quote=True)}\">{html.escape(it['title'])}</a>"
    if with_summary:
        s += "\n" + "\n".join("· " + html.escape(l) for l in it["lines"])
    return s


_sent = [0]


def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        print("텔레그램 설정 없음 - 발송 생략")
        return False
    silent = _sent[0] > 0          # 첫 메시지만 알림, 나머지는 무음
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": text,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": "true",
                            "disable_notification": "true" if silent else "false"},
                      timeout=20)
    if not r.ok:
        print("TG FAIL", r.status_code, r.text[:200])
    _sent[0] += 1
    time.sleep(1.1)
    return r.ok


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
    ok = bool(chunks)
    for n, c in enumerate(chunks, 1):
        head = f"📰 [{cat['name']}] {label} ({len(items)}/{cat['n']}건) {n}/{len(chunks)}\n\n"
        ok = tg_send(head + c.strip()) and ok
    return ok


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

    history = load_seen()                 # 이전 회차에 이미 보낸 기사
    seen, result = set(history), []
    print(f"이전 발송 이력 {len(history)}건 제외 대상")
    for cat in CATEGORIES:
        items = collect(cat, seen)
        enrich_and_summarize(items)
        result.append((cat, items))
        print(f"{cat['name']}: {len(items)}건 완료")

    write_html(result, label, stamp)
    wait_until(slot)
    for cat, items in result:
        if send_category(cat, items, label):          # 발송 성공한 분야만 이력에 기록
            ts = time.time()
            for it in items:
                history[it["key"]] = ts
            save_seen(history)
    tg_send(f'<a href="{html.escape(pages_url(), quote=True)}">📄 전체 요약 보기 ({label})</a>')


if __name__ == "__main__":
    main()
