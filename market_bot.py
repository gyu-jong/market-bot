#!/usr/bin/env python3
"""
market_bot.py - 시장 데이터 수집 -> HTML 리포트(그래프 포함) 생성 -> 카카오톡 "나에게 보내기" 알림
 
사용 예)
  python market_bot.py --slot 0700            # 0700 슬롯(A,B,E) 실행 후 카톡 발송
  python market_bot.py --targets D --no-send  # D만 실행, 발송 없이 docs/D.html 생성
GitHub Actions에서는 환경변수 SCHEDULE(cron 문자열)로 슬롯을 자동 판별합니다.
"""
import argparse, base64, io, json, os, pathlib, re, sys, time, datetime as dt
from zoneinfo import ZoneInfo
 
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yfinance as yf
 
KST = ZoneInfo("Asia/Seoul")
OUT = pathlib.Path(os.environ.get("OUT_DIR", "docs"))
PAGES_BASE = os.environ.get("PAGES_BASE_URL", "").rstrip("/")
 
from matplotlib import font_manager
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _f in ("NanumGothic", "AppleGothic", "Malgun Gothic"):
    if _f in _avail:
        plt.rcParams["font.family"] = _f
        break
 
plt.rcParams["axes.unicode_minus"] = False
 
# ───────────────────────── 스케줄 정의 (cron은 UTC = KST-9h) ─────────────────────────
SLOT_BY_CRON = {
    # 목표 발송시각보다 13~25분 먼저 시작(정각 혼잡·지연 회피) → 스크립트가 목표시각까지 기다렸다가 발송
    "35 21 * * 0-5": "0700",   # KST 월~토 0635 시작 → 0700 발송
    "47 22 * * 0-4": "0800",   # KST 월~금 0747 → 0800
    "37 23 * * 0-4": "0850",   # KST 월~금 0837 → 0850
    "35 23 * * 0-4": "0900",   # KST 월~금 0835 → 0900
    "47 0 * * 1-5": "1000",    # KST 월~금 0947 → 1000
    "17 3 * * 1-5": "1230",    # KST 월~금 1217 → 1230
    "47 4 * * 1-5": "1400",    # KST 월~금 1347 → 1400
    # (구 버전 정각 크론: 호환용)
    "0 22 * * 0-5": "0700", "0 23 * * 0-4": "0800", "50 23 * * 0-4": "0850",
    "0 0 * * 1-5": "0900", "0 1 * * 1-5": "1000", "30 3 * * 1-5": "1230", "0 5 * * 1-5": "1400",
}
TARGETS_BY_SLOT = {"0700": "ABE", "0900": "ABE",
                   "0800": "CD", "0850": "CD", "1000": "CD", "1230": "CD", "1400": "CD"}
TARGET_NAMES = {"A": "A 거시·시장지표", "B": "B 미국 ETF", "C": "C 한국 종목·ETF",
                "D": "D VAA 공격형", "E": "E 올시즌스"}
 
PERIODS = [("1일", None), ("1주", pd.DateOffset(weeks=1)), ("1개월", pd.DateOffset(months=1)),
           ("1분기", pd.DateOffset(months=3)), ("1년", pd.DateOffset(years=1)),
           ("2년", pd.DateOffset(years=2)), ("3년", pd.DateOffset(years=3))]
CHART_DAYS = int(os.environ.get("CHART_DAYS", "7"))  # 그래프에 표시할 최근 거래일 수
 
SYSTEM_PROMPT = """당신은 금융시장 데이터 분석 어시스턴트입니다.
입력된 수치 요약만 근거로 한국어 3문장 이내 코멘트를 작성하세요.
규칙: 입력에 없는 수치·뉴스·원인은 만들지 말 것. 추정은 '가능성' 표현을 쓸 것.
투자 권유(매수/매도 지시)는 하지 말고, 변화가 큰 항목과 주의점을 짚을 것."""
 
# ───────────────────────── 공통 유틸 ─────────────────────────
def f_num(v):
    if v is None or v != v:
        return "-"
    return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
 
def f_pct(v, diff=False):
    if v is None or v != v:
        return "-"
    arrow = "▲" if v > 0 else "▼" if v < 0 else "-"
    return f"{arrow}{abs(v):.2f}{'p' if diff else '%'}"
 
def cls(v):
    return "up" if (v == v and v > 0) else "dn" if (v == v and v < 0) else ""
 
def wk_no(d):
    """ISO 주차 번호 (예: W43)"""
    return f"W{d.isocalendar()[1]:02d}"
 
def iso_wk(d):
    """연도 포함 주차 (예: 2026-W43)"""
    y, w = d.isocalendar()[0], d.isocalendar()[1]
    return f"{y}-W{w:02d}"
 
def ym(d):
    return f"{d.year}-{d.month:02d}"
 
def period_label(name, bd, freq):
    """비교 기준 시점의 라벨: 일간=날짜/주차/월/분기/연, 주간=Week No., 월간=연-월"""
    if bd is None or bd != bd:
        return ""
    if freq == "M":
        return ym(bd)
    if freq == "W":
        return iso_wk(bd)
    if name == "1일":
        return f"{bd:%m-%d}"
    if name == "1주":
        return wk_no(bd)
    if name == "1개월":
        return f"{bd.month}월"
    if name == "1분기":
        return f"{bd.year}Q{(bd.month - 1) // 3 + 1}"
    return f"{bd.year}"
 
def asof_label(d, freq):
    if freq == "M":
        return ym(d)
    if freq == "W":
        return iso_wk(d)
    return f"{d:%m-%d}({wk_no(d)})"
 
def strip_tz(obj):
    if getattr(obj.index, "tz", None) is not None:
        obj.index = obj.index.tz_localize(None)
    return obj
 
def fetch_close(tickers, period="4y"):
    tickers = list(tickers)
    df = yf.download(tickers, period=period, auto_adjust=True, progress=False,
                     group_by="column", threads=True)
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
    if not isinstance(close, pd.DataFrame) or len(tickers) == 1:
        close = pd.DataFrame(close)
        close.columns = tickers[:1]
    return strip_tz(close)
 
def changes(s, diff=False, monthly=False, weekly=False):
    """기간별 증감. diff=True면 차이(금리 %p), 아니면 수익률(%). 기준 시점은 _bases 로 함께 반환."""
    s = s.dropna()
    last, d = s.iloc[-1], s.index[-1]
    out, bases = {}, {}
    for name, off in PERIODS:
        if off is None:
            bd = s.index[-2] if len(s) > 1 else None
        else:
            t = d - off
            bd = s.index.asof(t) if t >= s.index[0] else None
        base = np.nan
        if bd is not None and bd == bd:
            base = s.loc[bd]
            if isinstance(base, pd.Series):
                base = base.iloc[-1]
        if base != base or base == 0:
            out[name] = np.nan
        else:
            out[name] = (last - base) if diff else (last / base - 1) * 100
        bases[name] = bd
    if monthly or weekly:            # 월간·주간 지표는 일 단위 비교가 무의미
        out["1일"] = np.nan
    if monthly:
        out["1주"] = np.nan
    out["_d"], out["_bases"] = d, bases
    out["_freq"] = "M" if monthly else "W" if weekly else "D"
    return last, out
 
def spark_grid(series, title, cols=4, npts=None, unit="거래일"):
    items = [(n, s.dropna().tail(npts or CHART_DAYS)) for n, s in series.items() if s.dropna().size > 1]
    if not items:
        return ""
    rows = (len(items) + cols - 1) // cols
    fig, axs = plt.subplots(rows, cols, figsize=(cols * 2.8, rows * 1.9), squeeze=False)
    for ax, (nm, s) in zip(axs.flat, items):
        up = s.iloc[-1] >= s.iloc[0]
        ax.plot(range(len(s)), s.values, color="#d62728" if up else "#1f77b4", marker="o", ms=2.5)
        chg = (s.iloc[-1] / s.iloc[0] - 1) * 100 if s.iloc[0] else 0
        ax.set_title(f"{nm}\n{chg:+.1f}%", fontsize=8)
        ax.set_xticks([]); ax.tick_params(labelsize=6)
    for ax in list(axs.flat)[len(items):]:
        ax.axis("off")
    fig.suptitle(f"{title} (최근 {npts or CHART_DAYS}{unit})", fontsize=10)
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110); plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
 
def table_html(rows, diff=False):
    freq = rows[0][2].get("_freq", "D") if rows else "D"
    pers = [p for p, _ in PERIODS if any(r[2][p] == r[2][p] for r in rows)] or [p for p, _ in PERIODS]
    ref = max(rows, key=lambda r: r[2].get("_d", pd.Timestamp(0))) if rows else None
 
    def hdr(p):
        bd = ref[2].get("_bases", {}).get(p) if ref else None
        lab = period_label(p, bd, freq)
        return f"<th>{p}<br><small>{lab}</small></th>" if lab else f"<th>{p}</th>"
    head = ("<tr><th>항목</th><th>기준" + ("(월)" if freq == "M" else "(Week No.)" if freq == "W" else "(일·주차)")
            + "</th><th>현재</th>" + "".join(hdr(p) for p in pers) + "</tr>")
    body = ""
    for nm, last, ch in rows:
        d = ch.get("_d")
        asof = asof_label(d, freq) if d is not None else "-"
        tds = "".join(f"<td class='{cls(ch[p])}'>{f_pct(ch[p], diff)}</td>" for p in pers)
        body += f"<tr><td>{nm}</td><td>{asof}</td><td>{f_num(last)}</td>{tds}</tr>"
    return f"<div class='scroll'><table>{head}{body}</table></div>"
 
def block(title, series, diff=False, cols=4, sort_1d=False, monthly=False, weekly=False):
    rows = []
    for nm, s in series.items():
        s = s.dropna()
        if len(s) > 2:
            last, ch = changes(s, diff, monthly, weekly)
            rows.append((nm, last, ch))
    if sort_1d:
        rows.sort(key=lambda r: -(r[2]["1일"] if r[2]["1일"] == r[2]["1일"] else -1e9))
    lowfreq = monthly or weekly
    img = spark_grid({r[0]: series[r[0]] for r in rows}, title, cols,
                     npts=7 if lowfreq else None, unit="개월" if monthly else "주" if weekly else "거래일")
    html = f"<h2>{title}</h2>" + (f"<img src='{img}'>" if img else "") + table_html(rows, diff)
    return html, rows
 
def prices_block(title, mapping, **kw):
    close = fetch_close(mapping.keys())
    series = {nm: close[t] for t, nm in mapping.items() if t in close and close[t].dropna().size > 2}
    return block(title, series, **kw)
 
def df_html(df, title, n=None, open_=False):
    d = df.head(n) if n else df
    h = d.to_html(index=False, border=0, classes="t",
                  float_format=lambda x: f"{x:,.2f}", na_rep="-")
    return (f"<details {'open' if open_ else ''}><summary>{title} ({len(d)})</summary>"
            f"<div class='scroll'>{h}</div></details>")
 
# ───────────────────────── 대상 A ─────────────────────────
def fred(series_id):
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                     params={"id": series_id}, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0])).dropna()
    return s[s.index >= pd.Timestamp.now() - pd.DateOffset(years=4)]
 
# ECOS 통계코드(시장금리 일별 817Y002). 코드가 다르면 ECOS 사이트에서 확인 후 수정하세요.
ECOS_CODES = {"한 국고채3년": "010200000", "한 국고채10년": "010210000"}
 
def ecos(code):
    key = os.environ.get("ECOS_API_KEY")
    if not key:
        return None
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=365 * 4 + 5)).strftime("%Y%m%d")
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/2000/817Y002/D/{start}/{end}/{code}"
    rows = requests.get(url, timeout=30).json()["StatisticSearch"]["row"]
    return pd.Series({pd.to_datetime(r["TIME"]): float(r["DATA_VALUE"]) for r in rows}).sort_index()
 
SECTORS = {"XLK": "기술", "XLF": "금융", "XLE": "에너지", "XLV": "헬스케어", "XLY": "경기소비",
           "XLP": "필수소비", "XLI": "산업재", "XLB": "소재", "XLU": "유틸리티",
           "XLRE": "부동산", "XLC": "커뮤니케이션"}
 
# ── 데이터 수집 보조 ──
def _load_json(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default
 
def _save_json(p, obj):
    try:
        p = pathlib.Path(p); p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print("JSON 저장 실패:", p, e)
 
def cnn_fear_greed():
    start = (dt.date.today() - dt.timedelta(days=365 * 3)).strftime("%Y-%m-%d")
    r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata/" + start,
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                     timeout=30)
    r.raise_for_status()
    j = r.json()
    s = pd.Series({pd.to_datetime(d["x"], unit="ms").normalize(): float(d["y"])
                   for d in j["fear_and_greed_historical"]["data"]}).sort_index()
    return s, j.get("fear_and_greed", {}).get("rating", "")
 
def crack_spread():
    """미국 3-2-1 크랙스프레드($/bbl) = (2×휘발유 + 1×디젤 - 3×WTI)/3. 싱가포르 정제마진의 대용 지표."""
    c = fetch_close(["RB=F", "HO=F", "CL=F"]).dropna()
    return (2 * c["RB=F"] * 42 + c["HO=F"] * 42 - 3 * c["CL=F"]) / 3
 
def yf_items(title, items, **kw):
    """items: [(티커, 표시명, 배수)]"""
    series, missing = {}, []
    try:
        close = fetch_close([t for t, _, _ in items])
    except Exception as e:
        print("yfinance 실패", title, e); close = None
    for t, nm, mult in items:
        if close is not None and t in close and close[t].dropna().size > 2:
            series[nm] = close[t] * mult
        else:
            missing.append(nm)
    h, rows = block(title, series, **kw) if series else (f"<h2>{title}</h2>", [])
    if missing:
        h += "<p class='err'>조회 실패: " + ", ".join(missing) + "</p>"
    return h, rows, missing
 
def fred_items(title, items, **kw):
    """items: [(FRED 시리즈ID, 표시명)]"""
    series, missing = {}, []
    for sid, nm in items:
        try:
            s = fred(sid)
            if len(s) > 2:
                series[nm] = s; continue
        except Exception as e:
            print("FRED 실패", sid, e)
        missing.append(nm)
    h, rows = block(title, series, **kw) if series else (f"<h2>{title}</h2>", [])
    if missing:
        h += "<p class='err'>조회 실패: " + ", ".join(missing) + "</p>"
    return h, rows, missing
 
# ── 무료 시계열이 없는 지표: AI 웹검색 수집(캐시) + 수동 입력(우선) ──
AI_TTL_H = float(os.environ.get("AI_TTL_HOURS", "20"))
AI_CACHE = "data/ai_points.json"
MANUAL_FILE = "data/manual_inputs.json"
HIST_FILE = "data/point_history.json"
 
AI_GROUPS = {
    "pmi": {"desc": "주요 제조국 제조업 PMI (50 기준, 최근 월 확정/속보치 중 가장 최신).",
            "items": {"미국 ISM 제조업 PMI": "ISM Manufacturing PMI", "중국 NBS 제조업 PMI": "국가통계국 공식 제조업 PMI",
                      "유로존 HCOB 제조업 PMI": "HCOB Eurozone Manufacturing PMI",
                      "한국 제조업 PMI": "S&P Global South Korea Manufacturing PMI",
                      "대만 제조업 PMI": "S&P Global Taiwan Manufacturing PMI",
                      "일본 제조업 PMI": "S&P Global Japan Manufacturing PMI"}},
    "valsent": {"desc": "밸류에이션과 투자심리.",
                "items": {"S&P500 Forward P/E": "S&P 500 12개월 선행 PER(배), FactSet Earnings Insight 등",
                          "AAII 강세(%)": "AAII Investor Sentiment Survey 최신 주간 Bullish %",
                          "AAII 약세(%)": "같은 조사 Bearish %", "AAII 중립(%)": "같은 조사 Neutral %"}},
    "metidx": {"desc": "산업용 금속 지수.",
               "items": {"LMEX(LME Index)": "런던금속거래소 LME Index", "S&P GSCI Industrial Metals": "S&P GSCI 산업금속 지수",
                         "BCOMIN(Bloomberg 산업금속)": "Bloomberg Industrial Metals Subindex"}},
    "energy": {"desc": "에너지 가격(최근 종가/평가가격).",
               "items": {"싱가포르 복합 정제마진": "Singapore complex refining margin, $/bbl",
                         "두바이유 선물(DME Oman)": "DME Oman crude futures 근월물, $/bbl",
                         "나프타 MOPJ": "Naphtha MOPJ, $/t", "ICE 가스오일 선물": "ICE Low Sulphur Gasoil 근월물, $/t"}},
    "metals": {"desc": "비철·배터리 금속 가격(최근 종가/평가가격).",
               "items": {"LME 아연 3M": "LME 3개월 아연, $/t", "LME 니켈 3M": "LME 3개월 니켈, $/t",
                         "리튬 현물(탄산리튬)": "중국 배터리급 탄산리튬 현물, CNY/t",
                         "리튬 선물(GFEX)": "광저우선물거래소 탄산리튬 근월물, CNY/t",
                         "코발트 현물": "코발트 금속 현물, $/lb 또는 $/t (unit에 명시)",
                         "코발트 선물(CME)": "CME 코발트 근월물, unit에 명시"}},
    "freight": {"desc": "해운 운임지수.",
                "items": {"BDI(발틱운임지수)": "Baltic Dry Index", "SCFI(상하이컨테이너운임)": "Shanghai Containerized Freight Index",
                          "VLCC 운임(TD3C)": "VLCC 중동-중국 TD3C, WS 또는 $/day (unit에 명시)"}},
    "macro": {"desc": "한국 수출입(최근 월)과 중국 내수·투자 지표. 금액은 억 달러, note에 전년동월비(%)를 적을 것.",
              "items": {"한국 수출액": "산업통상부(MOTIE) 월간 수출입동향", "한국 수입액": "같은 자료",
                        "한국 무역수지": "같은 자료", "한국 반도체 수출액": "같은 자료(반도체)",
                        "중국 고정자산투자 누계 YoY(%)": "중국 국가통계국, 연초 누계 전년동기비",
                        "중국 소매판매 YoY(%)": "중국 국가통계국, 최근 월 전년동월비"}},
}
 
POINT_PLACE = {
    "A1": ["S&P500 Forward P/E"],
    "A2_PMI": ["미국 ISM 제조업 PMI", "중국 NBS 제조업 PMI", "유로존 HCOB 제조업 PMI",
               "한국 제조업 PMI", "대만 제조업 PMI", "일본 제조업 PMI"],
    "A2_METAL": ["LMEX(LME Index)", "S&P GSCI Industrial Metals", "BCOMIN(Bloomberg 산업금속)"],
    "A2_SENT": ["AAII 강세(%)", "AAII 약세(%)", "AAII 중립(%)"],
    "A4": ["싱가포르 복합 정제마진", "BDI(발틱운임지수)", "SCFI(상하이컨테이너운임)", "VLCC 운임(TD3C)",
           "리튬 현물(탄산리튬)", "코발트 현물"],
    "A5": ["두바이유 선물(DME Oman)", "나프타 MOPJ", "ICE 가스오일 선물", "LME 아연 3M", "LME 니켈 3M",
           "리튬 선물(GFEX)", "코발트 선물(CME)"],
    "A6": ["한국 수출액", "한국 수입액", "한국 무역수지", "한국 반도체 수출액",
           "중국 고정자산투자 누계 YoY(%)", "중국 소매판매 YoY(%)"],
}
 
def _ai_query(g):
    import anthropic
    client = anthropic.Anthropic()
    tmpl = {n: {"value": None, "unit": "", "as_of": "", "note": "", "source": ""} for n in g["items"]}
    hints = "\n".join(f"- {n}: {h}" for n, h in g["items"].items())
    prompt = (f"오늘은 {dt.date.today():%Y-%m-%d}입니다. 웹 검색으로 아래 지표의 가장 최근 공표 수치를 찾아 JSON만 출력하세요.\n"
              f"{g['desc']}\n{hints}\n"
              "규칙: 1) 검색 결과에서 직접 확인한 수치만 사용합니다. 찾지 못하면 value를 null로 두고, 추정·기억·계산으로 채우지 마세요. "
              "2) value는 숫자, unit은 단위, as_of는 기준일/월(YYYY-MM-DD 또는 YYYY-MM), note는 20자 이내 보충(예: 전년동월비 +3.1%), "
              "source는 출처 URL. 3) 설명 문장 없이 아래 형식의 JSON 객체 하나만 출력합니다.\n"
              + json.dumps(tmpl, ensure_ascii=False))
    msgs = [{"role": "user", "content": prompt}]
    text = ""
    for _ in range(4):
        r = client.messages.create(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=2000,
                                   tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
                                   messages=msgs)
        text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        if r.stop_reason != "pause_turn":
            break
        msgs = msgs + [{"role": "assistant", "content": r.content}]
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    clean = {}
    for n in g["items"]:
        rec = data.get(n) or {}
        try:
            v = float(rec.get("value")) if rec.get("value") is not None else None
        except (TypeError, ValueError):
            v = None
        clean[n] = {"value": v, "unit": str(rec.get("unit", ""))[:12], "as_of": str(rec.get("as_of", ""))[:10],
                    "note": str(rec.get("note", ""))[:40], "source": str(rec.get("source", ""))[:200]}
    return clean
 
def collect_points():
    """{표시명: record}. 우선순위: 수동입력 > AI검색(신선) > AI검색(캐시, stale)"""
    cache = _load_json(AI_CACHE, {})
    now = dt.datetime.now(dt.timezone.utc)
    use_ai = bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("AI_POINTS", "1") != "0"
    out = {}
    for gid, g in AI_GROUPS.items():
        ent = cache.get(gid)
        fresh = False
        if ent:
            try:
                fresh = (now - dt.datetime.fromisoformat(ent["ts"])).total_seconds() < AI_TTL_H * 3600
            except Exception:
                fresh = False
        if use_ai and not fresh:
            try:
                data = _ai_query(g)
                if any(v["value"] is not None for v in data.values()):
                    ent = {"ts": now.isoformat(), "data": data}; cache[gid] = ent; fresh = True
            except Exception as e:
                print(f"AI 수집 실패 [{gid}]:", repr(e)[:200])
        if ent:
            for n, rec in ent["data"].items():
                out[n] = dict(rec, src="AI" if fresh else "AI(캐시)")
    if use_ai:
        _save_json(AI_CACHE, cache)
    for n, rec in _load_json(MANUAL_FILE, {}).items():       # 수동 입력이 항상 우선
        if isinstance(rec, dict) and rec.get("value") is not None:
            out[n] = dict({"unit": "", "as_of": "", "note": "", "source": ""}, **{k: v for k, v in rec.items() if k != "src"}, src="수동")
    return out
 
def update_history(points):
    h = _load_json(HIST_FILE, {})
    for n, rec in points.items():
        if rec.get("value") is None:
            continue
        L = h.setdefault(n, [])
        if not L or L[-1]["as_of"] != rec.get("as_of", ""):
            L.append({"as_of": rec.get("as_of", ""), "value": rec["value"]})
            h[n] = L[-36:]
    _save_json(HIST_FILE, h)
    return h
 
def prev_change(h, n):
    L = h.get(n, [])
    if len(L) >= 2 and L[-2]["value"]:
        return (L[-1]["value"] / L[-2]["value"] - 1) * 100, L[-2]["as_of"]
    return None, ""
 
POINT_WEEKLY = {"S&P500 Forward P/E", "AAII 강세(%)", "AAII 약세(%)", "AAII 중립(%)", "SCFI(상하이컨테이너운임)"}
POINT_MONTHLY = set(POINT_PLACE["A2_PMI"]) | set(POINT_PLACE["A6"])
 
def point_freq(n):
    return "W" if n in POINT_WEEKLY else "M" if n in POINT_MONTHLY else "D"
 
def _pt_date(as_of):
    return pd.Timestamp(as_of if len(as_of) >= 10 else as_of[:7] + "-01")
 
def point_label(n, as_of):
    """표 표시용: 주간=2026-W43 (10-22), 월간=2026-09, 일간=10-23 (W43)"""
    if not as_of:
        return ""
    try:
        f, d = point_freq(n), _pt_date(as_of)
        return ym(d) if f == "M" else f"{iso_wk(d)} ({d:%m-%d})" if f == "W" else f"{d:%m-%d} ({wk_no(d)})"
    except Exception:
        return as_of
 
def point_tag(n, as_of):
    """카톡 요약용 짧은 표기: 주간=2026-W43, 월간=2026-09, 일간=10-23"""
    if not as_of:
        return ""
    try:
        f, d = point_freq(n), _pt_date(as_of)
        return ym(d) if f == "M" else iso_wk(d) if f == "W" else f"{d:%m-%d}"
    except Exception:
        return as_of[-7:]
 
def points_html(title, names, points, hist):
    rows, miss = "", []
    for n in names:
        rec = points.get(n)
        if not rec or rec.get("value") is None:
            miss.append(n)
            rows += f"<tr><td>{n}</td><td class='err' colspan='5'>조회불가 (data/manual_inputs.json 에 수동 입력 가능)</td></tr>"
            continue
        ch, pas = prev_change(hist, n)
        chs = f"{f_pct(ch)} (직전 {point_label(n, pas)})" if ch is not None else "-"
        src = rec.get("source", "")
        link = f"<a href='{src}'>링크</a>" if src.startswith("http") else ""
        rows += (f"<tr><td>{n}</td><td>{f_num(rec['value'])} {rec.get('unit', '')}</td><td>{point_label(n, rec.get('as_of', ''))}</td>"
                 f"<td class='{cls(ch if ch is not None else float('nan'))}'>{chs}</td>"
                 f"<td>{rec.get('src', '')}{'·검증필요' if rec.get('src', '').startswith('AI') else ''}</td>"
                 f"<td>{rec.get('note', '')} {link}</td></tr>")
    head = "<tr><th>항목</th><th>값</th><th>기준(주차·월)</th><th>직전 대비</th><th>출처</th><th>비고</th></tr>"
    return f"<h2>{title}</h2><div class='scroll'><table>{head}{rows}</table></div>", miss
 
def f_pct1(v, diff=False):
    if v is None or v != v:
        return "-"
    arrow = "▲" if v > 0 else "▼" if v < 0 else "-"
    return f"{arrow}{abs(v):.1f}{'p' if diff else '%'}"
 
# 카톡 요약(brief)에 항상 넣는 핵심 항목
A_KEY = {"미국 M2", "달러지수", "원/달러", "원/엔(100엔)", "S&P500", "나스닥", "코스피", "S&P500 Forward P/E",
         "미국 ISM 제조업 PMI", "중국 NBS 제조업 PMI", "VIX", "CNN Fear&Greed", "AAII 강세(%)",
         "미 국채10년", "한 국고채10년", "WTI(현물)", "브렌트(현물)", "금(현물)", "구리(선물)",
         "BDI(발틱운임지수)", "한국 수출액", "한국 반도체 수출액", "산업금속 ETF(DBB)"}
 
RSI_IDX = [("S&P500", "^GSPC"), ("다우", "^DJI"), ("나스닥", "^IXIC"), ("코스피", "^KS11"), ("코스닥", "^KQ11")]
PE_NAME = "S&P500 Forward P/E"
 
def wilder_rsi(s, n=14):
    """Wilder RSI(n). 데이터가 n+1개 미만이면 빈 시리즈."""
    s = s.dropna()
    if len(s) < n + 1:
        return pd.Series(dtype=float)
    d = s.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    r = 100 - 100 / (1 + au / ad)
    return r.where(ad != 0, 100.0).dropna()
 
def _last_of(s, kind):
    """주봉(금요일 마감)·월봉(월말) 종가. 진행 중인 주·월의 최근 값을 포함."""
    if kind == "W":
        return s.resample("W-FRI").last().dropna()
    try:
        return s.resample("ME").last().dropna()
    except ValueError:                       # 구버전 pandas
        return s.resample("M").last().dropna()
 
def rsi_zone(v):
    if v != v:
        return ""
    return "과매수" if v >= 70 else "과매도" if v <= 30 else "중립"
 
def _rsi_cell(v, tag=""):
    if v != v:
        return "<td>-</td>"
    c = "up" if v >= 70 else "dn" if v <= 30 else ""
    return f"<td class='{c}'>{v:.1f} {rsi_zone(v)}{(' <small>' + tag + '</small>') if tag else ''}</td>"
 
def rsi_html(hist):
    """A1 RSI(14): 일간·주간(Week No.)·월간. Forward P/E 는 누적된 주간 이력으로 계산."""
    close = fetch_close([t for _, t in RSI_IDX])
    rows, miss = "", []
    d_all, w_line, d_line, over, under = None, [], [], [], []
    for nm, t in RSI_IDX:
        s = close[t].dropna() if t in close else pd.Series(dtype=float)
        if len(s) < 40:
            miss.append(f"RSI {nm}")
            rows += f"<tr><td>{nm}</td><td class='err' colspan='5'>조회 실패</td></tr>"
            continue
        rd, rw, rm = wilder_rsi(s), wilder_rsi(_last_of(s, "W")), wilder_rsi(_last_of(s, "M"))
        dl = s.index[-1]
        d_all = dl if d_all is None or dl > d_all else d_all
        dv = rd.iloc[-1] if len(rd) else np.nan
        pv = rd.iloc[-6] if len(rd) >= 6 else np.nan
        wv = rw.iloc[-1] if len(rw) else np.nan
        mv = rm.iloc[-1] if len(rm) else np.nan
        diff5 = dv - pv
        rows += (f"<tr><td>{nm}</td>{_rsi_cell(dv, f'{dl:%m-%d}({wk_no(dl)})')}"
                 f"<td class='{cls(diff5)}'>{f_pct(diff5, True)}</td>"
                 f"{_rsi_cell(wv, iso_wk(dl))}{_rsi_cell(mv, ym(dl))}</tr>")
        d_line.append(f"{nm} {dv:.0f}" if dv == dv else f"{nm} -")
        w_line.append(f"{nm} {wv:.0f}" if wv == wv else f"{nm} -")
        if dv == dv and dv >= 70: over.append(nm)
        if dv == dv and dv <= 30: under.append(nm)
    # Forward P/E: 주간 이력으로 RSI
    pe_note, pe_w = "", None
    L = hist.get(PE_NAME, []) if isinstance(hist, dict) else []
    try:
        ser = pd.Series({_pt_date(x["as_of"]): float(x["value"]) for x in L if x.get("as_of")}).sort_index()
    except Exception:
        ser = pd.Series(dtype=float)
    if len(ser) >= 15:
        rp = wilder_rsi(ser)
        pe_w = rp.iloc[-1] if len(rp) else np.nan
        rows += (f"<tr><td>{PE_NAME}</td><td>-</td><td>-</td>{_rsi_cell(pe_w, iso_wk(ser.index[-1]))}<td>-</td></tr>")
    else:
        rows += (f"<tr><td>{PE_NAME}</td><td class='muted' colspan='4'>주간 이력 {len(ser)}/15주 — 매주 값이 쌓이면 "
                 f"자동 계산 (data/point_history.json 에 과거 주간값을 넣으면 즉시 계산)</td></tr>")
        miss.append(f"RSI {PE_NAME}(이력 {len(ser)}/15주)")
    head = ("<tr><th>지수</th><th>일간 RSI<br><small>기준일(주차)</small></th><th>5거래일 전 대비</th>"
            "<th>주간 RSI<br><small>Week No.</small></th><th>월간 RSI<br><small>월</small></th></tr>")
    html = (f"<h2>A1 RSI(14) — 일간·주간(Week No.)·월간</h2><div class='scroll'><table>{head}{rows}</table></div>"
            "<p class='muted'>과매수 ≥70, 과매도 ≤30 (Wilder). 주간·월간은 진행 중인 주·월의 최근 종가를 포함합니다.</p>")
    tagd = f"{d_all:%m-%d}" if d_all is not None else ""
    S = []
    if d_line:
        extra = (f" ※과매수 {','.join(over)}" if over else "") + (f" ※과매도 {','.join(under)}" if under else "")
        S.append({"text": f"RSI일({tagd}) " + " ".join(d_line) + extra, "key": True})
        S.append({"text": f"RSI주({iso_wk(d_all)}) " + " ".join(w_line)
                          + (f" FwdPE {pe_w:.0f}" if pe_w is not None and pe_w == pe_w else ""), "key": True})
    return html, S, miss
 
def target_A():
    parts, S, miss_all = [], [], []      # S: [{"text","key"}]
    def add_rows(rows, monthly=False, diff=False, weekly=False):
        for nm, last, ch in rows:
            v = ch["1개월"] if monthly else ch["1주"] if weekly else ch["1일"]
            d = ch.get("_d")
            tag = f"({ym(d)})" if monthly and d is not None else f"({iso_wk(d)})" if weekly and d is not None else ""
            S.append({"text": f"{nm} {f_num(last)}{tag} {'월' if monthly else '주' if weekly else ''}{f_pct1(v, diff)}",
                      "key": nm in A_KEY})
    def run_yf(title, items, monthly=False, **kw):
        try:
            h, rows, miss = yf_items(title, items, **kw)
        except Exception as e:
            parts.append(f"<h2>{title}</h2><p class='err'>조회 실패: {e}</p>"); return
        parts.append(h); miss_all.extend(miss); add_rows(rows, monthly)
    def run_fred(title, items, monthly=False, weekly=False, **kw):
        try:
            h, rows, miss = fred_items(title, items, monthly=monthly, weekly=weekly, **kw)
        except Exception as e:
            parts.append(f"<h2>{title}</h2><p class='err'>조회 실패: {e}</p>"); return
        parts.append(h); miss_all.extend(miss); add_rows(rows, monthly, weekly=weekly)
 
    points = {}
    try:
        points = collect_points()
    except Exception as e:
        print("포인트 지표 수집 실패:", repr(e)[:200])
    hist = update_history(points)
    def run_points(title, key):
        h, miss = points_html(title, POINT_PLACE[key], points, hist)
        parts.append(h); miss_all.extend(miss)
        for n in POINT_PLACE[key]:
            rec = points.get(n)
            if rec and rec.get("value") is not None:
                u = rec.get("unit", "")
                S.append({"text": f"{n.split('(')[0]} {f_num(rec['value'])}{u if len(u) <= 4 else ''}"
                                  f"({point_tag(n, rec.get('as_of', ''))})", "key": n in A_KEY})
 
    # ── A1 환율/주가 ──
    run_yf("A1 달러·환율", [("DX-Y.NYB", "달러지수", 1), ("KRW=X", "원/달러", 1), ("EURKRW=X", "원/유로", 1),
                         ("JPYKRW=X", "원/엔(100엔)", 100)])
    run_yf("A1 주가지수", [("^GSPC", "S&P500", 1), ("^DJI", "다우", 1), ("^IXIC", "나스닥", 1),
                        ("^KS11", "코스피", 1), ("^KQ11", "코스닥", 1)])
    run_fred("A1 미국 M2 통화량 (월간, 십억$)", [("M2SL", "미국 M2")], monthly=True)
    run_fred("A1 미국 M2 통화량 (주간, 십억$, 비계절조정)", [("WM2NS", "미국 M2(주간)")], weekly=True)
    run_points("A1 밸류에이션", "A1")
    try:
        h_r, s_r, m_r = rsi_html(hist)
        parts.append(h_r); S.extend(s_r); miss_all.extend(m_r)
    except Exception as e:
        parts.append(f"<h2>A1 RSI</h2><p class='err'>조회 실패: {e}</p>"); miss_all.append("RSI")
    # ── A2 지수/심리 ──
    run_points("A2 주요 제조국 제조업 PMI", "A2_PMI")
    run_yf("A2 산업용 금속 (ETF 대용)", [("DBB", "산업금속 ETF(DBB)", 1)])
    run_points("A2 산업용 금속지수 (LMEX·GSCI·BCOMIN)", "A2_METAL")
    run_yf("A2 변동성·심리 (VIX)", [("^VIX", "VIX", 1)])
    try:
        fg, rating = cnn_fear_greed()
        h, rows = block(f"A2 CNN Fear & Greed (현재 등급: {rating})", {"CNN Fear&Greed": fg})
        parts.append(h); add_rows(rows)
    except Exception as e:
        parts.append(f"<h2>A2 CNN Fear & Greed</h2><p class='err'>조회 실패: {e}</p>"); miss_all.append("CNN Fear&Greed")
    run_points("A2 AAII 투자자 심리", "A2_SENT")
    # ── A3 금리 ──
    rates = {}
    for sid, nm in (("DGS10", "미 국채10년"), ("DGS3", "미 국채3년")):
        try: rates[nm] = fred(sid)
        except Exception as e: print("FRED 실패", sid, e)
    for nm, code in ECOS_CODES.items():
        try:
            s = ecos(code)
            if s is not None: rates[nm] = s
        except Exception as e: print("ECOS 실패", nm, e)
    if rates:
        h, rows = block("A3 금리 (증감은 %p)", rates, diff=True, cols=4)
        parts.append(h); add_rows(rows, diff=True)
    else:
        parts.append("<h2>A3 금리</h2><p class='err'>조회 실패</p>")
    # ── A4 현물 ──
    run_fred("A4 에너지 현물 (일간, FRED/EIA)",
             [("DCOILWTICO", "WTI(현물)"), ("DCOILBRENTEU", "브렌트(현물)"), ("DHHNGSP", "천연가스 HH(현물)"),
              ("DHOILNYH", "난방유 NYH(현물)"), ("DDFUELNYH", "가스오일·디젤 NYH(현물, 대용)")])
    try:
        cs = crack_spread()
        h, rows = block("A4 정제마진 대용: 미국 3-2-1 크랙스프레드 ($/bbl)", {"크랙스프레드(3-2-1)": cs}, cols=4)
        parts.append(h); add_rows(rows)
    except Exception as e:
        parts.append(f"<h2>A4 크랙스프레드</h2><p class='err'>조회 실패: {e}</p>"); miss_all.append("크랙스프레드")
    run_fred("A4 두바이유·곡물·금속 현물 (월간, IMF 시세 / 발표 1~2개월 지연)",
             [("POILDUBUSDM", "두바이유(현물, 월평균)"), ("PMAIZMTUSDM", "옥수수(현물)"),
              ("PCOTTINDUSDM", "면화(현물)"), ("PSUGAISAUSDM", "설탕(현물)"), ("PSOYBUSDM", "대두(현물)"),
              ("PWHEAMTUSDM", "소맥(현물)"), ("PCOPPUSDM", "구리(현물)"), ("PIORECRUSDM", "철광석(현물)"),
              ("PALUMUSDM", "알루미늄(현물)"), ("PZINCUSDM", "아연(현물)"), ("PNICKUSDM", "니켈(현물)")],
             monthly=True)
    run_yf("A4 귀금속·지수·운임·리튬(대용 ETF)",
           [("XAUUSD=X", "금(현물)", 1), ("XAGUSD=X", "은(현물)", 1), ("^SOX", "반도체지수(SOX)", 1),
            ("^TWII", "대만가권지수", 1), ("BDRY", "건화물 운임 ETF(BDRY, BDI 대용)", 1),
            ("LIT", "리튬·배터리 ETF(LIT, 리튬 대용)", 1),
            ("DBA", "곡물·농산물 ETF(DBA, 곡물지수 대용)", 1)])
    run_points("A4 정제마진·운임·리튬·코발트", "A4")
    # ── A5 선물 ──
    run_yf("A5 에너지 선물",
           [("CL=F", "WTI(선물)", 1), ("BZ=F", "브렌트(선물)", 1), ("RB=F", "휘발유(선물)", 1),
            ("HO=F", "디젤·난방유·가스오일(HO, ULSD)", 1), ("NG=F", "천연가스(선물)", 1)])
    run_yf("A5 곡물·농산물 선물",
           [("ZC=F", "옥수수(선물)", 1), ("CT=F", "면화(선물)", 1), ("SB=F", "설탕(선물)", 1),
            ("ZS=F", "대두(선물)", 1), ("ZW=F", "소맥(선물)", 1),
            ("DBA", "곡물·농산물 ETF(DBA, 선물 바스켓)", 1)])
    run_yf("A5 금속 선물",
           [("GC=F", "금(선물)", 1), ("SI=F", "은(선물)", 1), ("HG=F", "구리(선물)", 1),
            ("ALI=F", "알루미늄(선물)", 1), ("TIO=F", "철광석(선물)", 1)])
    run_points("A5 그 외 선물 (두바이유·나프타·가스오일·LME·리튬·코발트)", "A5")
    # ── A6 실물·섹터 ──
    run_points("A6 한국 수출입·반도체 수출, 중국 투자·내수", "A6")
    try:
        h6, sect = yf_items("A6 미국 산업별 ETF (1일 등락순)", [(k, v, 1) for k, v in SECTORS.items()],
                            sort_1d=True, cols=4)[:2]
        parts.append(h6)
    except Exception as e:
        sect = []
        parts.append(f"<h2>A6 산업별 ETF</h2><p class='err'>조회 실패: {e}</p>")
 
    # ── 카톡 요약 ──
    mode = os.environ.get("A_SUMMARY_MODE", "brief").strip().lower()
    lines = [s["text"] for s in S if mode == "full" or s["key"]]
    _now = dt.datetime.now(KST)
    lines.insert(0, f"기준 {_now:%m-%d} ({iso_wk(_now)})")
    if sect:
        lines.append("섹터강세 " + ", ".join(f"{r[0]}{f_pct1(r[2]['1일'])}" for r in sect[:3]))
        lines.append("섹터약세 " + ", ".join(f"{r[0]}{f_pct1(r[2]['1일'])}" for r in sect[-3:]))
    if miss_all:
        lines.append(f"조회불가 {len(miss_all)}개(리포트 확인)")
    if mode != "full":
        lines.append("※전체 항목은 리포트 링크에서 확인")
    return "".join(parts), "\n".join(lines)
 
# ───────────────────────── 대상 B (미국 ETF) ─────────────────────────
def us_etf_universe():
    syms = set()
    for url, col in (("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
                     ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol")):
        df = pd.read_csv(io.StringIO(requests.get(url, timeout=60).text), sep="|")
        df = df[df["ETF"] == "Y"]
        if "Test Issue" in df:
            df = df[df["Test Issue"] == "N"]
        syms |= set(df[col].astype(str))
    return sorted(s for s in syms if re.fullmatch(r"[A-Z]{1,5}", s))
 
def target_B():
    syms = us_etf_universe()
    lim = int(os.environ.get("MAX_US_ETFS", "0"))
    if lim:
        syms = syms[:lim]
    C, V = [], []
    for i in range(0, len(syms), 200):
        try:
            df = yf.download(syms[i:i + 200], period="1y", auto_adjust=False, progress=False,
                             threads=True, group_by="column")
            C.append(df["Close"]); V.append(df["Volume"])
        except Exception as e:
            print("chunk 실패", i, e)
    close = strip_tz(pd.concat(C, axis=1)).dropna(axis=1, how="all")
    vol = strip_tz(pd.concat(V, axis=1))[close.columns]
    last, prev = close.iloc[-1], close.iloc[-2]
    d = pd.DataFrame({"종목": close.columns, "종가": last.values,
                      "등락률(%)": ((last / prev - 1) * 100).values,
                      "거래량": vol.iloc[-1].values, "전일거래량": vol.iloc[-2].values,
                      "52주고가": close.max().values})
    d["거래대금($)"] = d["종가"] * d["거래량"]
    d["거래증가율(%)"] = (d["거래량"] / d["전일거래량"].replace(0, np.nan) - 1) * 100
    d = d.dropna(subset=["등락률(%)", "거래량"])
    d = d[(d["종가"] >= float(os.environ.get("MIN_PRICE", "5"))) &
          (d["거래대금($)"] >= float(os.environ.get("MIN_DOLLAR_VOL", "1000000")))]
    asof = close.index[-1].strftime("%Y-%m-%d")
    cols = ["종목", "종가", "등락률(%)", "거래량", "거래증가율(%)", "거래대금($)"]
    hi = d[d["종가"] >= d["52주고가"] * 0.9999].sort_values("등락률(%)", ascending=False)
    surge = d[d["전일거래량"] >= 100000]
    html = (f"<p>기준일 {asof} / 유동성 필터 통과 ETF {len(d)}개 (미국은 상·하한가 제도가 없어 상승·하락률 순위로 대체)</p>"
            + df_html(d.sort_values("등락률(%)", ascending=False)[cols], "상승 상위", 50, True)
            + df_html(d.sort_values("등락률(%)")[cols], "하락 상위", 50)
            + df_html(hi[cols], "52주 신고가", 50)
            + df_html(d.sort_values("거래량", ascending=False)[cols], "거래량 상위", 30)
            + df_html(surge.sort_values("거래증가율(%)", ascending=False)[cols], "거래증가율 상위(전일 10만주 이상)", 30))
    fmt = lambda df: ", ".join(f"{r['종목']} {r['등락률(%)']:+.1f}%" for _, r in df.iterrows())
    summ = (f"{asof}\n▲상승5: " + fmt(d.sort_values("등락률(%)", ascending=False).head(5)) +
            "\n▼하락5: " + fmt(d.sort_values("등락률(%)").head(5)) +
            f"\n52주신고가 {len(hi)}개: " + ", ".join(hi["종목"].head(5)) +
            "\n거래량TOP5: " + ", ".join(d.sort_values("거래량", ascending=False)["종목"].head(5)) +
            "\n거래증가TOP5: " + ", ".join(surge.sort_values("거래증가율(%)", ascending=False)["종목"].head(5)))
    return html, summ
 
# ───────────────────────── 대상 C (한국) ─────────────────────────
KR_US_RE = re.compile("미국|S&P|나스닥|NASDAQ|다우존스|필라델피아|빅테크|테슬라|엔비디아|애플|마이크로소프|구글|아마존|팔란티어|브로드컴|FANG|뉴욕")
 
def _to_name(v, default):
    """pykrx 버전에 따라 이름이 str/Series/DataFrame으로 올 수 있어 문자열로 통일."""
    try:
        if isinstance(v, pd.DataFrame):
            v = v.iloc[0, 0] if v.size else ""
        elif isinstance(v, pd.Series):
            v = v.iloc[0] if len(v) else ""
        v = str(v).strip()
        return v if v and v.lower() not in ("nan", "none") else default
    except Exception:
        return default
 
def etf_names(stock, tickers):
    p = pathlib.Path("data/etf_names.json")
    try:
        cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        cache = {}
    fails = 0
    for t in tickers:
        t = str(t)
        if t in cache and cache[t] and cache[t] != t:
            continue
        if fails >= 5:                      # 연속 실패 시 더 시도하지 않고 코드로 대체
            cache.setdefault(t, t)
            continue
        try:
            name = _to_name(stock.get_etf_ticker_name(t), t)
            cache[t] = name
            fails = 0 if name != t else fails + 1
        except Exception:
            cache.setdefault(t, t)
            fails += 1
    try:
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps({str(k): str(v) for k, v in cache.items()}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("ETF 이름 캐시 저장 실패:", e)
    return {str(k): str(v) for k, v in cache.items()}
 
def kr_sections(df, label, d, hi_fetch, topn_hi=150):
    cols = ["종목", "종가", "등락률(%)", "거래량", "거래증가율(%)", "거래대금"]
 
  
    df = df.dropna(subset=["등락률(%)"]).copy()
    if df.empty:
        return f"<h3>{label}</h3><p class='err'>데이터 없음(KRX 응답이 비어 있음)</p>", f"{label} 데이터 없음"
  
    cand = df.sort_values("거래대금", ascending=False).head(topn_hi)
    start = (dt.datetime.strptime(d, "%Y%m%d") - dt.timedelta(days=365)).strftime("%Y%m%d")
    hi_list = []
    for t, row in cand.iterrows():
        try:
            if row["종가"] >= hi_fetch(start, d, t)["종가"].max() * 0.9999:
                hi_list.append(t)
        except Exception:
            pass
    surge = df[(df["전일거래량"] > 0) & (df["거래대금"] >= 1e8)]
    html = (f"<h3>{label}</h3>"
            + df_html(df[df["등락률(%)"] >= 29.0].sort_values("등락률(%)", ascending=False)[cols], "상한가", None, True)
            + df_html(df[df["등락률(%)"] <= -29.0].sort_values("등락률(%)")[cols], "하한가", None, True)
            + df_html(df.loc[df.index.intersection(hi_list)][cols], f"52주 신고가 (거래대금 상위 {topn_hi} 중)")
            + df_html(df.sort_values("등락률(%)", ascending=False)[cols], "상승 상위", 50)
            + df_html(df.sort_values("등락률(%)")[cols], "하락 상위", 50)
            + df_html(df.sort_values("거래량", ascending=False)[cols], "거래량 상위", 30)
            + df_html(surge.sort_values("거래증가율(%)", ascending=False)[cols], "거래증가율 상위", 30))
    ups = df[df["등락률(%)"] >= 29.0]; dns = df[df["등락률(%)"] <= -29.0]
    f2 = lambda x: ", ".join(f"{r['종목']} {r['등락률(%)']:+.1f}%" for _, r in x.iterrows())
    summ = (f"■{label}\n상한{len(ups)}: " + ", ".join(ups["종목"].head(3)) +
            f" / 하한{len(dns)}: " + ", ".join(dns["종목"].head(3)) +
            "\n▲" + f2(df.sort_values("등락률(%)", ascending=False).head(5)) +
            "\n▼" + f2(df.sort_values("등락률(%)").head(5)) +
            f"\n52주신고가 {len(hi_list)}개: " + ", ".join(df.loc[df.index.intersection(hi_list), "종목"].head(4)) +
            "\n거래량TOP3: " + ", ".join(df.sort_values("거래량", ascending=False)["종목"].head(3)) +
            "\n거래증가TOP3: " + ", ".join(surge.sort_values("거래증가율(%)", ascending=False)["종목"].head(3)))
    return html, summ
 
def target_C():
    import traceback
    from pykrx import stock
    parts, sums, errs, cache = [], [], [], {}
 
    def need(label, r):
        if r is None or (hasattr(r, "empty") and r.empty):
            raise RuntimeError(f"{label}: 빈 응답")
        return r
 
    def run(label, fn):
        try:
            h, sm = fn()
            parts.append(h); sums.append(sm)
        except Exception as ex:
            traceback.print_exc()
            errs.append(f"{label}: {type(ex).__name__}: {ex}")
 
    today = dt.datetime.now(KST).strftime("%Y%m%d")
    d = stock.get_nearest_business_day_in_a_week(today)
    if not isinstance(d, str):
        raise RuntimeError(f"영업일 조회 실패(KRX 응답 이상): {d!r}")
    pdy = stock.get_nearest_business_day_in_a_week(
        (dt.datetime.strptime(d, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d"))
    if not isinstance(pdy, str):
        raise RuntimeError(f"전영업일 조회 실패(KRX 응답 이상): {pdy!r}")
 
    def do_stock():
        cur = need("코스피 당일시세", stock.get_market_ohlcv(d, market="KOSPI"))
        prv = need("코스피 전일시세", stock.get_market_ohlcv(pdy, market="KOSPI"))
        nm = need("종목명", stock.get_market_price_change_by_ticker(pdy, d, market="KOSPI"))["종목명"]
        s = pd.DataFrame({"종목": nm, "종가": cur["종가"], "등락률(%)": cur["등락률"],
                          "거래량": cur["거래량"], "전일거래량": prv["거래량"],
                          "거래대금": cur["거래대금"]}).dropna(subset=["종가"])
        s["거래증가율(%)"] = (s["거래량"] / s["전일거래량"].replace(0, np.nan) - 1) * 100
        return kr_sections(s, "C1 코스피 종목", d,
                           lambda a, b, t: stock.get_market_ohlcv_by_date(a, b, t))
 
    def make_etf():
        if "e" not in cache:
            ec = need("ETF 당일시세", stock.get_etf_ohlcv_by_ticker(d))
            ep = need("ETF 전일시세", stock.get_etf_ohlcv_by_ticker(pdy))
            names = etf_names(stock, [str(i) for i in ec.index])
            e = pd.DataFrame({"종목": [str(names.get(str(t), t)) for t in ec.index],
                              "종가": ec["종가"].values,
                              "등락률(%)": ((ec["종가"] / ep["종가"].reindex(ec.index) - 1) * 100).values,
                              "거래량": ec["거래량"].values,
                              "전일거래량": ep["거래량"].reindex(ec.index).values,
                              "거래대금": ec["거래대금"].values}, index=ec.index)
            e = e[e["종가"] > 0].copy()
            e["거래증가율(%)"] = (e["거래량"] / e["전일거래량"].replace(0, np.nan) - 1) * 100
            cache["e"] = e
        return cache["e"]
 
    efetch = lambda a, b, t: stock.get_etf_ohlcv_by_date(a, b, t)
    run("C1 코스피 종목", do_stock)
    run("C1 한국 ETF", lambda: kr_sections(make_etf(), "C1 한국 ETF", d, efetch, 100))
    run("C2 미국투자 한국 ETF", lambda: kr_sections(
        make_etf()[make_etf()["종목"].str.contains(KR_US_RE)], "C2 미국증시 투자 한국 ETF", d, efetch, 60))
 
    if not parts:
        raise RuntimeError(" | ".join(errs) or "원인 불명")
    err_html = ("<p class='err'>일부 실패: " + "<br>".join(errs) + "</p>") if errs else ""
    return f"<p>기준일 {d}</p>{err_html}" + "".join(parts), f"{d}\n" + "\n".join(sums)
 
# ───────────────────────── 대상 D (VAA 공격형) ─────────────────────────
VAA_OFF = {"SPY": "미국주식", "EFA": "선진국주식", "EEM": "신흥국주식", "AGG": "미국혼합채권"}
VAA_DEF = {"LQD": "미국회사채", "IEF": "미국중기국채", "SHY": "미국단기국채"}
 
def momentum(s, asof):
    s = s[:asof].dropna(); last, d = s.iloc[-1], s.index[-1]
    r = {m: last / s.asof(d - pd.DateOffset(months=m)) - 1 for m in (1, 3, 6, 12)}
    return r, 12 * r[1] + 4 * r[3] + 2 * r[6] + r[12]
 
def vaa_pick(close, asof):
    sc, rets = {}, {}
    for t in list(VAA_OFF) + list(VAA_DEF):
        rets[t], sc[t] = momentum(close[t], asof)
    if all(sc[t] > 0 for t in VAA_OFF):
        pick, mode = max(VAA_OFF, key=lambda t: sc[t]), "공격"
    else:
        pick, mode = max(VAA_DEF, key=lambda t: sc[t]), "방어"
    return pick, mode, sc, rets
 
def target_D():
    names = {**VAA_OFF, **VAA_DEF}
    close = fetch_close(names.keys())
    h, _ = block("D1 VAA 자산군", {f"{t}({n})": close[t] for t, n in names.items()}, cols=4)
    last = close.index[-1]
    pick, mode, sc, rets = vaa_pick(close, last)
    prev_end = last.replace(day=1) - pd.Timedelta(days=1)
    ppick, pmode, _, _ = vaa_pick(close, prev_end)
    rows = ""
    for t in names:
        r = rets[t]
        rows += (f"<tr><td>{t}({names[t]})</td>" +
                 "".join(f"<td class='{cls(r[m])}'>{r[m]*100:+.2f}%</td>" for m in (1, 3, 6, 12)) +
                 f"<td>{sc[t]*100:+.2f}</td></tr>")
    tbl = ("<h2>D2 모멘텀 (점수 = 12×1M + 4×3M + 2×6M + 1×12M)</h2><div class='scroll'><table>"
           "<tr><th>자산</th><th>1M</th><th>3M</th><th>6M</th><th>12M</th><th>점수</th></tr>" + rows + "</table></div>")
    changed = "변경됨" if ppick != pick else "동일"
    rule = (f"<h2>D3 매수/매도 규칙 평가</h2><p>규칙: 공격자산 4개 모두 점수>0 → 점수 최고 공격자산 100%, "
            f"하나라도 ≤0 → 점수 최고 안전자산 100%.</p>"
            f"<p><b>현재 신호({last:%Y-%m-%d}): {mode}모드 → {pick}({names[pick]}) 100%</b></p>"
            f"<p>직전 월말({prev_end:%Y-%m-%d}) 신호: {pmode}모드 → {ppick} ({changed})</p>"
            f"<p class='muted'>원전략은 월말 1회 리밸런싱입니다. 장중 수치는 참고용입니다.</p>")
    lines = [f"VAA {mode}모드 → {pick}({names[pick]})", f"직전월말 {ppick}, 신호 {changed}"]
    for t in names:
        r = rets[t]
        lines.append(f"{t} 1M{r[1]*100:+.1f} 3M{r[3]*100:+.1f} 6M{r[6]*100:+.1f} 12M{r[12]*100:+.1f} 점수{sc[t]*100:+.1f}")
    return h + tbl + rule, "\n".join(lines)
 
# ───────────────────────── 대상 E (올시즌스) ─────────────────────────
ALLSEASONS = {"SPY": "미국주식", "TLT": "미국장기채", "GLD": "금", "SHY": "미국단기채"}
 
def target_E():
    close = fetch_close(ALLSEASONS.keys())
    last, d = close.iloc[-1], close.index[-1]
    R = {}
    for t in ALLSEASONS:
        s = close[t].dropna()
        R[t] = {m: s.iloc[-1] / s.asof(d - pd.DateOffset(months=m)) - 1 for m in (1, 3, 6, 12)}
    port = {m: sum(0.25 * R[t][m] for t in ALLSEASONS) for m in (1, 3, 6, 12)}
    rows = ""
    for t, n in ALLSEASONS.items():
        rows += f"<tr><td>{t}({n}) 25%</td>" + "".join(
            f"<td class='{cls(R[t][m])}'>{R[t][m]*100:+.2f}%</td>" for m in (1, 3, 6, 12)) + "</tr>"
    rows += "<tr><td><b>포트폴리오(균등 25%)</b></td>" + "".join(
        f"<td class='{cls(port[m])}'><b>{port[m]*100:+.2f}%</b></td>" for m in (1, 3, 6, 12)) + "</tr>"
    tot = sum(1 + R[t][12] for t in ALLSEASONS)
    w = {t: (1 + R[t][12]) / tot for t in ALLSEASONS}
    need = any(abs(w[t] - 0.25) > 0.05 for t in ALLSEASONS)
    drift = " / ".join(f"{t} {w[t]*100:.1f}%" for t in ALLSEASONS)
    h, _ = block("E1 올시즌스 구성 자산", {f"{t}({n})": close[t] for t, n in ALLSEASONS.items()}, cols=4)
    html = (h + "<h2>수익률 (1·3·6·12개월)</h2><div class='scroll'><table><tr><th>자산</th><th>1M</th><th>3M</th>"
            "<th>6M</th><th>12M</th></tr>" + rows + "</table></div>"
            f"<p>12개월 전 25%씩 매수 후 방치 시 현재 비중: {drift}<br>"
            f"리밸런싱(±5%p 밴드 기준): <b>{'필요' if need else '불필요'}</b></p>"
            "<p class='muted'>포트폴리오 수익률은 자산별 수익률의 25% 가중 단순합(근사)입니다.</p>")
    lines = [f"{t} 1M{R[t][1]*100:+.1f} 3M{R[t][3]*100:+.1f} 6M{R[t][6]*100:+.1f} 12M{R[t][12]*100:+.1f}" for t in ALLSEASONS]
    lines.append(f"합산 1M{port[1]*100:+.1f} 3M{port[3]*100:+.1f} 6M{port[6]*100:+.1f} 12M{port[12]*100:+.1f}")
    lines.append(f"현재비중 {drift}")
    lines.append(f"리밸런싱 {'필요' if need else '불필요'}")
    return html, "\n".join(lines)
 
# ───────────────────────── 리포트 / 카카오 ─────────────────────────
CSS = """body{font-family:'Noto Sans KR',-apple-system,sans-serif;max-width:980px;margin:auto;padding:12px;font-size:14px}
h1{font-size:18px}h2{font-size:16px;margin-top:22px}img{max-width:100%}.scroll{overflow-x:auto}
table{border-collapse:collapse;font-size:12px;white-space:nowrap}td,th{border:1px solid #ddd;padding:3px 7px;text-align:right}
td:first-child,th:first-child{text-align:left}.up{color:#d62728}.dn{color:#1f77b4}.err{color:#c00}.muted{color:#888;font-size:12px}
summary{cursor:pointer;font-weight:600;margin:8px 0}.ai{background:#f5f7fa;padding:8px 12px;border-radius:6px}"""
 
def ai_comment(text):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    try:
        import anthropic
        m = anthropic.Anthropic().messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=400,
            system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}])
        return m.content[0].text.strip()
    except Exception as e:
        print("AI 코멘트 실패", e); return ""
 
def write_page(t, slot, body, summ):
    OUT.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(KST)
    ai = ai_comment(summ)
    ai_html = f"<div class='ai'>{ai}</div>" if ai else ""
    html = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style>"
            f"<title>{TARGET_NAMES[t]}</title></head><body><h1>{TARGET_NAMES[t]} · 슬롯 {slot} · {now:%Y-%m-%d %H:%M} KST ({iso_wk(now)})</h1>"
            f"{ai_html}{body}<p class='muted'>데이터: Yahoo Finance(yfinance), FRED, 한국은행 ECOS, KRX(pykrx). "
            f"지연·오류 가능. 투자 판단의 근거로 단독 사용 금지.</p></body></html>")
    (OUT / f"{t}.html").write_text(html, encoding="utf-8")
    idx = "".join(f"<li><a href='{k}.html'>{v}</a></li>" for k, v in TARGET_NAMES.items())
    (OUT / "index.html").write_text(f"<!doctype html><meta charset='utf-8'><h1>시장 리포트</h1><ul>{idx}</ul>", encoding="utf-8")
 
def update_gh_secret(name, value):
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not (pat and repo):
        print(f"WARN: 새 refresh token 발급됨. {name} 시크릿을 수동 갱신하세요."); return
    from nacl import encoding, public
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    k = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=20).json()
    box = public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder()))
    enc = base64.b64encode(box.encrypt(value.encode())).decode()
    requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/{name}", headers=h,
                 json={"encrypted_value": enc, "key_id": k["key_id"]}, timeout=20).raise_for_status()
 
def kakao_access_token():
    data = {"grant_type": "refresh_token", "client_id": os.environ["KAKAO_REST_API_KEY"],
            "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"]}
    if os.environ.get("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("refresh_token"):
        update_gh_secret("KAKAO_REFRESH_TOKEN", j["refresh_token"])
    return j["access_token"]
 
def kakao_send(token, text, link=None):
    url = link or "https://www.kakao.com"
    tpl = {"object_type": "text", "text": text[:199],
           "link": {"web_url": url, "mobile_web_url": url}, "button_title": "리포트 보기"}
    r = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                      headers={"Authorization": f"Bearer {token}"},
                      data={"template_object": json.dumps(tpl, ensure_ascii=False)}, timeout=20)
    print("kakao:", r.status_code, r.text[:200])
    r.raise_for_status()
 
# ───────────────────────── main ─────────────────────────
def chunk_text(text, limit=150):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur); cur = line[:limit]
        else:
            cur = f"{cur}\n{line}" if cur else line[:limit]
    if cur:
        chunks.append(cur)
    return chunks
 
FUNCS = {"A": target_A, "B": target_B, "C": target_C, "D": target_D, "E": target_E}
 
def nearest_slot(now):
    hhmm = now.hour * 100 + now.minute
    return min(TARGETS_BY_SLOT, key=lambda s: abs(int(s) - hhmm))
 
SUMM_FILE = pathlib.Path("out/summaries.json")
 
def build(slot, targets):
    items = []
    for t in targets:
        if t not in FUNCS:
            print(f"알 수 없는 대상: {t}", file=sys.stderr)
            continue
        try:
            body, summ = FUNCS[t]()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{t}] 실패:", repr(e), file=sys.stderr)
            body, summ = f"<p class='err'>조회 실패: {e}</p>", f"조회 실패: {type(e).__name__} {str(e)[:70]}"
        write_page(t, slot, body, summ)
        items.append({"t": t, "summ": summ})
    SUMM_FILE.parent.mkdir(exist_ok=True)
    SUMM_FILE.write_text(json.dumps({"slot": slot, "items": items}, ensure_ascii=False), encoding="utf-8")
    return items
 
def should_wait(no_wait):
    """GitHub 예약(SCHEDULE) 또는 외부 호출(WAIT_FOR_SLOT=true)일 때만 목표시각까지 대기."""
    if no_wait:
        return False
    return bool(os.environ.get("SCHEDULE")) or os.environ.get("WAIT_FOR_SLOT", "").strip().lower() == "true"
 
def slot_target(slot):
    now = dt.datetime.now(KST)
    return now.replace(hour=int(slot[:2]), minute=int(slot[2:]), second=0, microsecond=0)
 
def wait_until_slot(slot, max_wait_min=60):
    """목표시각까지 남았으면 기다린다. 이미 지났으면(지연) 즉시 진행."""
    delta = (slot_target(slot) - dt.datetime.now(KST)).total_seconds()
    if 0 < delta <= max_wait_min * 60:
        print(f"[대기] {slot}까지 {int(delta)}초 대기", flush=True)
        time.sleep(delta)
    else:
        print(f"[대기 없음] 목표시각 대비 {int(-delta)}초 경과", flush=True)
 
def send_all(slot, items, show_late=False):
    token = kakao_access_token()
    late = int((dt.datetime.now(KST) - slot_target(slot)).total_seconds() // 60)
    # 정시 발송 모드(예약/외부 호출)일 때만 '지연' 표시. 수동 테스트에서는 표시하지 않음
    tag = f" +{late}분 지연" if (show_late and 10 <= late <= 360) else ""
    for it in items:
        t, summ = it["t"], it["summ"]
        link = f"{PAGES_BASE}/{t}.html" if PAGES_BASE else None
        parts = chunk_text(summ)
        for i, body_txt in enumerate(parts, 1):
            head = f"[{slot}{tag}] {TARGET_NAMES[t]} ({i}/{len(parts)})\n"
            try:
                kakao_send(token, head + body_txt, link)
            except Exception as e:
                print("카톡 발송 실패:", e, file=sys.stderr)
        if link:
            try:
                kakao_send(token, f"[{slot}] {TARGET_NAMES[t]} 전체 리포트(그래프·상위50)\n{link}", link)
            except Exception as e:
                print("링크 발송 실패:", e, file=sys.stderr)
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot"); ap.add_argument("--targets")
    ap.add_argument("--no-send", action="store_true", help="발송 없이 리포트만 생성")
    ap.add_argument("--build-only", action="store_true", help="리포트·요약만 생성(발송 안 함)")
    ap.add_argument("--send-only", action="store_true", help="out/summaries.json 을 읽어 발송만")
    ap.add_argument("--no-wait", action="store_true", help="목표시각 대기 없이 즉시 발송(수동 테스트용)")
    a = ap.parse_args()
    now = dt.datetime.now(KST)
    print(f"[시작] KST {now:%Y-%m-%d %H:%M:%S} / SCHEDULE='{os.environ.get('SCHEDULE', '')}'", flush=True)
 
    if a.send_only:
        data = json.loads(SUMM_FILE.read_text(encoding="utf-8"))
        slot = data["slot"]
        timed = should_wait(a.no_wait)
        if timed:
            wait_until_slot(slot)
        send_all(slot, data["items"], show_late=timed)
        return
 
    slot = a.slot or SLOT_BY_CRON.get(os.environ.get("SCHEDULE", "")) or nearest_slot(now)
    raw = (a.targets or "").strip().upper()
    if raw in ("", "AUTO"):
        raw = TARGETS_BY_SLOT.get(slot, "ABE")
    targets = raw.replace(",", "").replace(" ", "")
    print(f"[슬롯] {slot} / 대상 {targets}", flush=True)
    items = build(slot, targets)
    if a.build_only or a.no_send:
        return
    timed = should_wait(a.no_wait)
    if timed:
        wait_until_slot(slot)
    send_all(slot, items, show_late=timed)
 
if __name__ == "__main__":
    main()
 
