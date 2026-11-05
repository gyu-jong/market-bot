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

---

# 정시 실행 설정 (외부 타이머 cron-job.org)

GitHub 자체 예약(schedule)은 정각 부근에서 수 분~수십 분 늦거나 누락될 수 있어, 외부 타이머가 정해진 시각에 GitHub를 호출하는 방식을 씁니다.
(워크플로우의 `schedule` 블록은 주석 처리돼 있습니다. 외부 타이머와 동시에 켜면 메시지가 중복 발송됩니다.)

## 1) 호출용 토큰 만들기 (GitHub)
계정 Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate
- Repository access: Only select repositories → `market-bot`
- Permissions → Repository permissions → **Actions: Read and write**
- 생성된 `github_pat_...` 값을 복사해 둡니다. (시크릿 갱신용 `GH_PAT`과는 별개의 토큰입니다)

## 2) cron-job.org 가입 후 작업 7개 만들기
각 작업(Create cronjob)의 공통 설정
- URL: `https://api.github.com/repos/깃허브아이디/market-bot/actions/workflows/market.yml/dispatches`
- Time zone: **Asia/Seoul**
- Advanced → Request method: **POST**
- Advanced → Headers (3개)
  - `Accept` : `application/vnd.github+json`
  - `Authorization` : `Bearer github_pat_복사한토큰`
  - `X-GitHub-Api-Version` : `2022-11-28`
- Advanced → Request body (슬롯마다 slot만 바꿉니다)
  `{"ref":"main","inputs":{"slot":"0700","targets":"AUTO","wait":"true"}}`

| 발송 시각(slot) | 호출 시각(KST) = 발송 60분 전 | 요일 |
|---|---|---|
| 0700 | 06:00 | 월~토 |
| 0800 | 07:00 | 월~금 |
| 0850 | 07:50 | 월~금 |
| 0900 | 08:00 | 월~금 |
| 1000 | 09:00 | 월~금 |
| 1230 | 11:30 | 월~금 |
| 1400 | 13:00 | 월~금 |

호출을 일부러 60분 일찍 보내는 이유: GitHub 실행 시작이 늦어져도 흡수하기 위해서입니다. 실행이 시작되면 스크립트는 **발송 20분 전(C·D는 8분 전)까지 기다렸다가** 그때 데이터를 모으고, **slot 시각에 맞춰** 카톡을 보냅니다. 수집이 한도(기본 25분)를 넘으면 해당 대상만 "수집 시간 초과"로 표시하고 나머지는 정시에 보냅니다.
비공개(private) 저장소는 무료 실행 시간이 월 2,000분이라, 대기 시간 때문에 부족할 수 있습니다. 이 경우 `TRIGGER_LEAD_MIN`을 30으로 줄이고 호출 시각도 30분 전으로 바꾸세요. 성공하면 cron-job.org 실행 기록에 HTTP 204가 보입니다.

## 3) 지연 원인 읽는 법
정시보다 10분 이상 늦으면 카톡 머리에 `[0700 +19분(시작+40·수집4)]`처럼 표시됩니다.
- 시작+N: 호출 예정 시각보다 N분 늦게 실행이 시작됨 (타이머/GitHub 문제)
- 수집M: 데이터 수집에 M분 걸림 (수집이 느린 경우)

## 속도 관련 환경변수 (선택)
- `AI_DEADLINE_SEC`(기본 240): AI 웹검색 수집의 총 시간 상한. 넘으면 남은 그룹은 캐시를 씁니다.
- `B_UNIV_TOP`(기본 1500): 미국 ETF 조회 대상을 거래대금 상위 N개로 제한(주 1회 전체 갱신).


---

# 자동 실행이 늦거나 안 올 때: 원인 점검 (3분)

수동 실행(Run workflow)은 빨리 오는데 자동 실행만 몇 시간씩 늦다면, 코드가 아니라 **자동 실행을 시작시키는 쪽**의 문제입니다.

## 1) cron-job.org 실행 기록 확인
작업을 열면 실행 기록(History)에 시각과 응답 코드가 나옵니다.
- **204**: 정상 (GitHub가 실행을 시작함)
- 그 밖의 숫자(401, 403, 404, 422): 호출이 실패한 것 → 아래 2)로 원인 확인

## 2) 호출을 직접 시험 (D만 가볍게 실행, 즉시 발송)
Mac/Linux 터미널:
```
curl -i -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer github_pat_토큰" -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/깃허브아이디/market-bot/actions/workflows/market.yml/dispatches -d '{"ref":"main","inputs":{"slot":"0700","targets":"D","wait":"false"}}'
```
Windows 명령 프롬프트(한 줄):
```
curl.exe -i -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer github_pat_토큰" -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/깃허브아이디/market-bot/actions/workflows/market.yml/dispatches -d "{\"ref\":\"main\",\"inputs\":{\"slot\":\"0700\",\"targets\":\"D\",\"wait\":\"false\"}}"
```
맨 위 줄의 숫자로 판단합니다.
| 응답 | 의미와 조치 |
|---|---|
| 204 | 정상. 몇 분 안에 D 결과가 카톡으로 옴 |
| 401 | 토큰이 틀렸거나 만료 → 토큰 다시 만들기 |
| 403 | 토큰에 Actions 쓰기 권한 없음 → 권한 `Actions: Read and write` 확인 |
| 404 | 아이디·저장소·파일명(market.yml)이 틀렸거나 토큰이 이 저장소 접근 권한이 없음 |
| 422 `Unexpected inputs provided` | 저장소의 market.yml 이 **옛 버전**임(wait 입력이 없음) → 새 파일로 교체 |
| 422 `No ref found` | 브랜치 이름이 main이 아님 → 본문의 `"ref"`를 실제 브랜치명으로 |
| 422 `does not have 'workflow_dispatch'` | market.yml 이 옛 버전 → 새 파일로 교체 |

## 3) Actions 탭의 실행 목록 확인
각 실행 옆의 Event 열이 `workflow_dispatch`(cron-job.org 호출)인지 `schedule`(GitHub 자체 예약)인지 봅니다. `schedule`만 보이고 정시 `workflow_dispatch`가 없다면 cron-job.org 호출이 실패하고 있는 것입니다.

## 같은 시각에 두 번 오지 않나요?
cron-job.org 호출과 GitHub 예약이 둘 다 켜져 있어도 **같은 슬롯은 한 번만** 발송됩니다. 먼저 온 실행이 `data/sent/날짜_슬롯.txt` 표식을 올려 선점하고, 늦게 온 실행은 "이미 발송됨"으로 건너뜁니다. (수동 실행은 항상 발송됩니다.)
