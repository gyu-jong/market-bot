#!/usr/bin/env python3
"""
market_bot.py - 시장 데이터 수집 -> HTML 리포트(그래프 포함) 생성 -> 카카오톡 "나에게 보내기" 알림

사용 예)
  python market_bot.py --slot 0700            # 0700 슬롯(A,B,E) 실행 후 카톡 발송
  python market_bot.py --targets D --no-send  # D만 실행, 발송 없이 docs/D.html 생성
GitHub Actions에서는 환경변수 SCHEDULE(cron 문자열)로 슬롯을 자동 판별합니다.
"""
import argparse, base64, io, json, os, pathlib, re, sys, datetime as dt
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
    "0 22 * * 0-5": "0700",   # KST 월~토 0700
    "0 23 * * 0-4": "0800",   # KST 월~금 0800
    "50 23 * * 0-4": "0850",
    "0 0 * * 1-5": "0900",
    "0 1 * * 1-5": "1000",
    "30 3 * * 1-5": "1230",
    "0 5 * * 1-5": "1400",
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

def changes(s, diff=False):
    """기간별 증감. diff=True면 차이(금리 %p), 아니면 수익률(%)."""
    s = s.dropna()
    last, d = s.iloc[-1], s.index[-1]
    out = {}
    for name, off in PERIODS:
        if off is None:
            base = s.iloc[-2] if len(s) > 1 else np.nan
        else:
            t = d - off
            base = s.asof(t) if t >= s.index[0] else np.nan
        if base != base or base == 0:
            out[name] = np.nan
        else:
            out[name] = (last - base) if diff else (last / base - 1) * 100
    return last, out

def spark_grid(series, title, cols=4):
    items = [(n, s.dropna().tail(CHART_DAYS)) for n, s in series.items() if s.dropna().size > 1]
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
    fig.suptitle(f"{title} (최근 {CHART_DAYS}거래일)", fontsize=10)
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110); plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def table_html(rows, diff=False):
    head = "<tr><th>항목</th><th>현재</th>" + "".join(f"<th>{p}</th>" for p, _ in PERIODS) + "</tr>"
    body = ""
    for nm, last, ch in rows:
        tds = "".join(f"<td class='{cls(ch[p])}'>{f_pct(ch[p], diff)}</td>" for p, _ in PERIODS)
        body += f"<tr><td>{nm}</td><td>{f_num(last)}</td>{tds}</tr>"
    return f"<div class='scroll'><table>{head}{body}</table></div>"

def block(title, series, diff=False, cols=4, sort_1d=False):
    rows = []
    for nm, s in series.items():
        s = s.dropna()
        if len(s) > 2:
            last, ch = changes(s, diff)
            rows.append((nm, last, ch))
    if sort_1d:
        rows.sort(key=lambda r: -(r[2]["1일"] if r[2]["1일"] == r[2]["1일"] else -1e9))
    img = spark_grid({r[0]: series[r[0]] for r in rows}, title, cols)
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

A_GROUPS = [
    ("A1 환율", {"KRW=X": "원/달러", "EURKRW=X": "원/유로", "JPYKRW=X": "원/엔(1엔)"}),
    ("A2 종합주가지수", {"^GSPC": "S&P500", "^DJI": "다우", "^IXIC": "나스닥",
                      "^KS11": "코스피", "^KQ11": "코스닥"}),
    ("A4 주요지표(선물·ETF 대용)", {"CL=F": "WTI유", "BZ=F": "브렌트유", "NG=F": "천연가스", "GC=F": "금",
                           "SI=F": "은", "^SOX": "반도체SOX", "BDRY": "물류(BDRY ETF)",
                           "ZC=F": "옥수수", "ZW=F": "밀", "ZS=F": "대두", "HG=F": "구리",
                           "SLX": "철강(SLX ETF)"}),
    ("A5 선물", {"CL=F": "원유", "NG=F": "천연가스", "ZC=F": "옥수수", "HG=F": "구리",
               "TIO=F": "철광석(조회실패 가능)"}),
]
SECTORS = {"XLK": "기술", "XLF": "금융", "XLE": "에너지", "XLV": "헬스케어", "XLY": "경기소비",
           "XLP": "필수소비", "XLI": "산업재", "XLB": "소재", "XLU": "유틸리티",
           "XLRE": "부동산", "XLC": "커뮤니케이션"}

def target_A():
    parts, key, sect = [], {}, []
    def add(h, rows):
        parts.append(h)
        for nm, last, ch in rows:
            key[nm] = (last, ch["1일"])
    for title, m in A_GROUPS:
        try:
            add(*prices_block(title, m))
        except Exception as e:
            parts.append(f"<h2>{title}</h2><p class='err'>조회 실패: {e}</p>")
    # A3 금리
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
        add(h, rows)
    else:
        parts.append("<h2>A3 금리</h2><p class='err'>조회 실패</p>")
    try:
        h6, sect = prices_block("A6 미국 산업별 ETF (1일 등락순)", SECTORS, sort_1d=True, cols=4)
        parts.append(h6)
    except Exception as e:
        parts.append(f"<h2>A6</h2><p class='err'>조회 실패: {e}</p>")
    lines = [f"{k} {f_num(v[0])} {f_pct(v[1])}" for k, v in key.items()]
    if sect:
        lines.append("섹터강세 " + ", ".join(f"{r[0]}{f_pct(r[2]['1일'])}" for r in sect[:3]))
        lines.append("섹터약세 " + ", ".join(f"{r[0]}{f_pct(r[2]['1일'])}" for r in sect[-3:]))
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

def etf_names(stock, tickers):
    p = pathlib.Path("data/etf_names.json")
    cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    for t in tickers:
        if t not in cache:
            try: cache[t] = stock.get_etf_ticker_name(t)
            except Exception: cache[t] = t
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache

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
            names = etf_names(stock, list(ec.index))
            e = pd.DataFrame({"종목": [str(names.get(t, t)) for t in ec.index],
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
            f"<title>{TARGET_NAMES[t]}</title></head><body><h1>{TARGET_NAMES[t]} · 슬롯 {slot} · {now:%Y-%m-%d %H:%M} KST</h1>"
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot"); ap.add_argument("--targets"); ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.now(KST)
    slot = a.slot or SLOT_BY_CRON.get(os.environ.get("SCHEDULE", "")) or nearest_slot(now)
    targets = (a.targets or TARGETS_BY_SLOT[slot]).upper().replace(",", "").replace(" ", "")
    token = None if a.no_send else kakao_access_token()

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
        if token:
            link = f"{PAGES_BASE}/{t}.html" if PAGES_BASE else None
            parts = chunk_text(summ)
            for i, body_txt in enumerate(parts, 1):
                head = f"[{slot}] {TARGET_NAMES[t]} ({i}/{len(parts)})\n"
                try:
                    kakao_send(token, head + body_txt, link)
                except Exception as e:
                    print("카톡 발송 실패:", e, file=sys.stderr)
            if link:
                try:
                    kakao_send(token, f"[{slot}] {TARGET_NAMES[t]} 전체 리포트(그래프·상위50)\n{link}", link)
                except Exception as e:
                    print("링크 발송 실패:", e, file=sys.stderr)

if __name__ == "__main__":
    main()
