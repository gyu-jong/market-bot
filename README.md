# 시장 리포트 → 카카오톡 자동 발송

## 동작 방식
GitHub Actions(cron) → `market_bot.py` 실행 → 데이터 수집·그래프 생성 → `docs/*.html` 리포트 게시(GitHub Pages)
→ 카카오톡 "나에게 보내기"로 **요약(200자) + 리포트 링크** 발송.

- 카카오톡 텍스트 메시지는 200자 제한이라, 상위 50개 종목표와 그래프는 웹페이지 링크로 제공합니다.
- 카카오 API는 전화번호(010-2732-4918)로 직접 발송할 수 없습니다. **해당 번호로 가입한 카카오 계정**으로 앱을 연결하면 그 계정의 "나와의 채팅"으로 도착합니다.

## 설치 순서
1. 이 폴더를 새 GitHub 저장소에 올립니다. (Pages 무료 사용은 public 저장소 기준. 데이터는 공개 시세뿐입니다.)
2. 저장소 Settings → Pages → Branch `main` / `/docs` 지정. 주소를 Settings → Variables → Actions에 `PAGES_BASE_URL`로 등록 (예: `https://아이디.github.io/저장소`).
3. 카카오 설정
   - developers.kakao.com → 앱 생성 → 카카오 로그인 활성화 → 동의항목 `talk_message`(카카오톡 메시지 전송) 설정 → Redirect URI에 `https://localhost` 등록.
   - 브라우저에서 아래 주소로 인가코드(`code`) 발급:
     `https://kauth.kakao.com/oauth/authorize?client_id={REST_API_KEY}&redirect_uri=https://localhost&response_type=code&scope=talk_message`
   - 토큰 발급:
     `curl -X POST https://kauth.kakao.com/oauth/token -d grant_type=authorization_code -d client_id={REST_API_KEY} -d redirect_uri=https://localhost -d code={CODE}`
   - 응답의 `refresh_token`을 저장합니다. (유효 60일. 만료 1개월 전부터 갱신 시 새 토큰이 발급되며, `GH_PAT`을 등록하면 시크릿을 자동 갱신합니다.)
4. Settings → Secrets → Actions 등록
   - 필수: `KAKAO_REST_API_KEY`, `KAKAO_REFRESH_TOKEN`
   - 선택: `KAKAO_CLIENT_SECRET`(앱에서 사용 설정한 경우), `ECOS_API_KEY`(한국 국채금리, ecos.bok.or.kr 무료 발급), `ANTHROPIC_API_KEY`(AI 코멘트), `GH_PAT`(repo 시크릿 쓰기 권한), `KRX_ID`/`KRX_PW`
5. Actions 탭 → `market-report` → Run workflow로 수동 테스트 (targets `ABCDE`).

## 로컬 테스트
```bash
pip install -r requirements.txt
python market_bot.py --targets AE --no-send     # docs/A.html, docs/E.html 생성
```

## 시간 슬롯 (KST)
| 시각 | 대상 | cron(UTC) |
|---|---|---|
| 0700, 0900 | A, B, E | `0 22 * * 0-5`, `0 0 * * 1-5` |
| 0800, 0850, 1000, 1230, 1400 | C, D | `0 23`, `50 23`, `0 1`, `30 3`, `0 5` |

GitHub cron은 정시보다 수 분~수십 분 늦을 수 있습니다. 0850처럼 정시성이 중요하면 항상 켜진 서버의 cron에서 `python market_bot.py --slot 0850`을 실행하세요.

## 알아둘 한계 (확인 필요)
- `pykrx`는 KRX 사이트 정책 변경으로 로그인(KRX_ID/PW)이 필요하거나 실패할 수 있습니다. 실패 시 C는 "조회 실패"로 표시됩니다.
- 0800·0850은 정규장 개장 전이라 C는 전일 종가 기준입니다. 한국 휴장일은 데이터가 직전 영업일 기준으로 표시됩니다.
- 물류지수는 BDI 대신 BDRY ETF, 철광석은 TIO=F(조회 실패 가능)/SLX ETF로 대체했습니다. 필요하면 유료 데이터로 교체하세요.
- ECOS 통계코드(`ECOS_CODES`)는 사이트에서 한 번 확인해 주세요.
- 미국 ETF·주식에는 상·하한가가 없어 B는 상승·하락률 순위로 대체했고, 한국(C)은 등락률 ±29% 이상을 상·하한가로 판정합니다.
- 그래프는 최근 7거래일 종가이며 `CHART_DAYS` 환경변수로 바꿉니다. "당일 중심 7일, 1일전 제외"를 다르게 해석하신다면 `spark_grid`를 수정하세요.
- 투자 판단용이 아닌 참고용 리포트입니다. 데이터 지연·오류가 있을 수 있습니다.

---

# 넣을 문구 (프롬프트)

## 1) 스크립트의 AI 코멘트용 (`SYSTEM_PROMPT`, 이미 코드에 포함)
```
당신은 금융시장 데이터 분석 어시스턴트입니다.
입력된 수치 요약만 근거로 한국어 3문장 이내 코멘트를 작성하세요.
규칙: 입력에 없는 수치·뉴스·원인은 만들지 말 것. 추정은 '가능성' 표현을 쓸 것.
투자 권유(매수/매도 지시)는 하지 말고, 변화가 큰 항목과 주의점을 짚을 것.
```

## 2) Claude 채팅/API에 직접 요청할 때 (웹 검색 가능 환경, 시각마다 대상 부분만 교체)
```
오늘 날짜와 현재 시각(KST)을 기준으로 아래 [대상]의 최신 데이터를 조회해 카카오톡용으로 요약해 주세요.

[공통 규칙]
- 시각은 hhmm 표기, 통화·단위·기준시각·출처 명시
- 비교: 1일, 1주, 1개월, 1분기, 1년, 2년, 3년 전 대비 증감률(%) (금리는 %p)
- 추이: 최근 7거래일 종가 그래프(그래프를 만들 수 없으면 ▁▂▃▅▇ 형태의 텍스트 스파크라인)
- 상승 ▲ / 하락 ▼ / 보합 -, 맨 위에 핵심 3줄 요약
- 조회하지 못한 항목은 추정하지 말고 "조회 실패"로 표시
- 전체 1,000자 이내, 넘으면 항목별로 나누어 출력

[대상 A] 환율(원/달러·유로·100엔), 지수(S&P500·다우·나스닥·코스피·코스닥), 금리(미·한 10년/3년 국채),
 주요지표(유가·천연가스·금·은·반도체지수·물류지수·곡물·구리·철광석), 선물(원유·천연가스·옥수수·구리·철광석),
 미국 11개 섹터 ETF 등락률 순위
[대상 B] 미국 ETF: 상승률 상위 50, 하락률 상위 50, 52주 신고가, 거래량 상위 30, 거래증가율 상위 30 (상·하한가 제도 없음 명시)
[대상 C] 한국: C1 코스피 종목·한국 ETF, C2 미국증시 투자 한국 ETF 각각
 상한가, 하한가, 52주 신고가, 상승률 상위 50, 하락률 상위 50, 거래량 상위 30, 거래증가율 상위 30
[대상 D] VAA 공격형: 공격 SPY·EFA·EEM·AGG, 안전 LQD·IEF·SHY. 1·3·6·12개월 수익률,
 모멘텀 점수 = 12×(1M)+4×(3M)+2×(6M)+1×(12M). 공격자산 4개 모두 점수>0이면 최고점 공격자산 100%,
 아니면 최고점 안전자산 100%. 현재 신호와 직전 월말 신호 대비 변경 여부 표시
[대상 E] 올시즌스: SPY 25%, TLT 25%, GLD 25%, SHY 25%. 1·3·6·12개월 수익률, 합산 수익률,
 현재 비중 편차와 리밸런싱 필요 여부(±5%p 기준)
```
