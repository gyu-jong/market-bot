# 시장 리포트 → 카카오톡 자동 발송

> **처음부터 다시 설정하려면 [`SETUP_FROM_SCRATCH.md`](SETUP_FROM_SCRATCH.md) 를 보세요.** 단계마다 확인 항목이 있습니다.
> 텔레그램 연결만 따로 점검하는 **`telegram-test`** 워크플로우(Actions 탭에서 Run workflow 버튼 한 번)도 들어 있습니다.

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

## 시간 슬롯 (KST) · 발송 대상
| 슬롯 | 요일 | 발송 대상 | 비고 |
|---|---|---|---|
| 0630 | 월~토 | A(지수·거시 전체), B(미국 ETF), D(VAA), E(올시즌스) | 미국 마감 결과 |
| 0830 | 월~금 | A(지수·거시 전체) | |
| 0800, 0930, 1030, 1300, 1400, 1500 | 월~금 | A1(환율·주가·RSI), A2(PMI·금속·심리), A3(금리), C(한국 종목·ETF) | 한국 시간대 |
| **1630** | 월~금 | **C 전체**(코스피 종목, 한국 ETF, 미국투자 한국 ETF + 코스피·코스닥 지수) | 장 마감 후 확정 수치 |
| 2400 (자정) | 월~금 | B, D, E | 미국 장 시작 직후라 **장중 수치** (당일 거래량·등락은 진행 중) |

- A = A1~A6 전체, A1·A2·A3 = 그 앞부분만 빠르게. 대상은 `--targets`에 `A1,A2,A3,C`처럼 지정할 수도 있습니다.
- 2400 은 다음 날 00:00 입니다. 미국 정규장은 겨울(표준시) 23:30 KST, 여름(서머타임) 22:30 KST 에 열립니다.

GitHub cron은 정시보다 수 분~수십 분 늦을 수 있습니다. 정시성이 중요하면 항상 켜진 서버의 cron에서 `python market_bot.py --slot 0800`처럼 실행하세요.

## 알아둘 한계 (확인 필요)
- `pykrx`는 KRX 사이트 정책 변경으로 로그인(KRX_ID/PW)이 필요하거나 실패할 수 있습니다. 실패 시 C는 "조회 실패"로 표시됩니다.
- 0800은 한국 정규장(09:00) 개장 전이라 C는 전일 종가 기준입니다. 한국 휴장일은 데이터가 직전 영업일 기준으로 표시됩니다.
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

# 정시 실행 설정 (cron-job.org) — 슬롯마다 두 가지 호출: 30분 전 + 정시

같은 슬롯을 두 번 호출합니다. 둘 다 카톡 결과가 오고, 머리말로 구분됩니다.
| 호출 방식 | 호출 시각 | 결과 도착 | 카톡 머리 |
|---|---|---|---|
| 30분 전 호출 | 발송 30분 전 | **정시**(실행이 먼저 시작해 수집을 끝내 두고 정시에 발송) | `[0630 30분전호출]` |
| 정시 호출 | 발송 시각 | 수집이 끝나는 대로(보통 수집 시간만큼 뒤) | `[0630 정시호출]` |

GitHub 자체 예약은 몇 시간씩 늦어서 꺼 두었습니다. (두 호출이 서로 보완합니다.)

## 1) 호출용 토큰 만들기 (GitHub)
계정 Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate
- Repository access: Only select repositories → `market-bot`
- Permissions → Repository permissions → **Actions: Read and write**
- 생성된 `github_pat_...` 값을 복사해 둡니다. (시크릿 갱신용 `GH_PAT`과는 별개의 토큰입니다)

## 2) cron-job.org 작업 만들기 (슬롯 9개 × 2가지 = 18개)
**먼저 기존 작업(60분 전 호출, lead·quiet 가 들어간 본문)은 모두 삭제하거나 아래 값으로 고칩니다.** 옛 본문이 남아 있으면 422 오류가 계속됩니다.

공통 설정 (한 번 만든 뒤 작업 복제(Duplicate)로 시각과 본문만 바꾸면 편합니다)
- URL: `https://api.github.com/repos/gyu-jong/market-bot/actions/workflows/market.yml/dispatches`
- Time zone: **Asia/Seoul**
- Advanced → Request method: **POST**
- Advanced → Headers (4개)
  - `Accept` : `application/vnd.github+json`
  - `Authorization` : `Bearer github_pat_복사한토큰`
  - `X-GitHub-Api-Version` : `2022-11-28`
  - `Content-Type` : `application/json`
- Advanced → **Save responses in job history** 켜기 (실패했을 때 GitHub의 오류 문장을 볼 수 있습니다)
- Request body: **`inputs`에는 slot 하나만** 넣습니다. 따옴표는 키보드의 곧은 따옴표(`"`)여야 합니다.
  - 30분 전 호출: `{"ref":"main","inputs":{"slot":"0630@30"}}`
  - 정시 호출 : `{"ref":"main","inputs":{"slot":"0630@0"}}`
  - 저장소의 기본 브랜치가 `main`이 아니면 `"ref"` 값을 그 이름으로 바꿉니다.

| slot | 30분 전 호출 (본문 `slot`) | 정시 호출 (본문 `slot`) | 요일 | 보내는 대상 |
|---|---|---|---|---|
| 0630 | 06:00 (`0630@30`) | 06:30 (`0630@0`) | 월~토 | A, B, D, E |
| 0800 | 07:30 (`0800@30`) | 08:00 (`0800@0`) | 월~금 | A1, A2, A3, C |
| 0830 | 08:00 (`0830@30`) | 08:30 (`0830@0`) | 월~금 | A |
| 0930 | 09:00 (`0930@30`) | 09:30 (`0930@0`) | 월~금 | A1, A2, A3, C |
| 1030 | 10:00 (`1030@30`) | 10:30 (`1030@0`) | 월~금 | A1, A2, A3, C |
| 1300 | 12:30 (`1300@30`) | 13:00 (`1300@0`) | 월~금 | A1, A2, A3, C |
| 1400 | 13:30 (`1400@30`) | 14:00 (`1400@0`) | 월~금 | A1, A2, A3, C |
| 1500 | 14:30 (`1500@30`) | 15:00 (`1500@0`) | 월~금 | A1, A2, A3, C |
| **1630** | 16:00 (`1630@30`) | 16:30 (`1630@0`) | 월~금 | C 전체 |
| 2400 | 23:30 (`2400@30`), 월~금 | 00:00 (`2400@0`), **화~토** | | B, D, E |

(2400 슬롯의 정시 호출은 다음 날 자정이라 요일이 화~토입니다.)

## 3) 잘 연결됐는지 확인
1. cron-job.org에서 작업의 **Test run**을 누르면 응답이 **204**여야 합니다. (오류면 응답 본문의 `message`를 보세요 → 아래 표)
2. 저장소 Actions 탭 왼쪽 목록에 **`market-report v9`** 이름이 보이고, 방금 호출한 실행이 생겨야 합니다. 이름에 v9가 없으면 올린 `market.yml`이 옛 버전입니다.

`slot` 하나만 쓰는 이유: 호출 본문에 저장소 파일에 없는 입력이 들어가면 GitHub가 422로 거절합니다. `slot`은 이 워크플로우의 모든 버전에 있어서 이 형태는 그 이유로 거절될 수 없습니다.

## 4) 결과 읽는 법
- 정시보다 10분 이상 늦으면 머리에 `[0630 30분전호출 +19분(시작+40·수집4)]`처럼 표시됩니다.
  - 시작+N: 호출 예정 시각보다 N분 늦게 실행이 시작됨 (타이머/GitHub 쪽)
  - 수집M: 데이터 수집에 M분 걸림
- 호출 방식별 도착 지연은 `docs/timing.html`(= `https://깃허브아이디.github.io/market-bot/timing.html`)에 자동으로 쌓입니다. 평균·최대 지연과 10분·30분 이내 비율이 방식별로 나옵니다.

## 속도 관련 환경변수 (선택)
- `AI_DEADLINE_SEC`(기본 240): AI 웹검색 수집의 총 시간 상한. 넘으면 남은 그룹은 캐시를 씁니다.
- `B_UNIV_TOP`(기본 1500): 미국 ETF 조회 대상을 거래대금 상위 N개로 제한(주 1회 전체 갱신).

---

# 호출이 오류(422 등)로 실패할 때

## 직접 호출해서 오류 문장 확인 (D만 가볍게 실행, 즉시 발송)
Mac/Linux 터미널:
```
curl -i -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer github_pat_토큰" -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/gyu-jong/market-bot/actions/workflows/market.yml/dispatches -d '{"ref":"main","inputs":{"slot":"0630","targets":"D","wait":"false"}}'
```
Windows 명령 프롬프트(한 줄):
```
curl.exe -i -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer github_pat_토큰" -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/gyu-jong/market-bot/actions/workflows/market.yml/dispatches -d "{\"ref\":\"main\",\"inputs\":{\"slot\":\"0630\",\"targets\":\"D\",\"wait\":\"false\"}}"
```
맨 위 줄의 숫자와 마지막 `message` 문장으로 판단합니다.
| 응답 | 의미와 조치 |
|---|---|
| 204 | 정상. 몇 분 안에 D 결과가 카톡으로 옴 |
| 401 | 토큰 인증 실패 → 아래 "401 Unauthorized일 때" 참고 |
| 403 | 토큰에 Actions 쓰기 권한 없음 → `Actions: Read and write` 확인 |
| 404 | 아이디·저장소·파일명(market.yml)이 틀렸거나 토큰이 이 저장소 접근 권한이 없음 |
| 422 `Unexpected inputs provided` | 본문에 slot 외 입력이 있거나, 저장소의 market.yml 이 옛 버전 → 본문을 slot 하나로, 파일을 새 버전으로 |
| 422 `No ref found` | 브랜치 이름이 main이 아님 → 본문의 `"ref"`를 실제 브랜치명으로 |
| 422 `does not have 'workflow_dispatch'` | market.yml 이 옛 버전이거나 파일 형식 오류 → 새 파일로 교체 |
| 422 `Invalid request` / `wasn't supplied` | 본문이 비었거나 JSON 형식 오류(굽은 따옴표 “ ” 포함 여부 확인) → 위 본문을 그대로 입력 |

## 같은 시각에 두 번 오지 않나요?
의도된 동작입니다. 슬롯마다 **30분 전 호출 결과**와 **정시 호출 결과**가 각각 한 번씩 옵니다. 같은 호출 방식이 두 번 실행돼도(재시도 등) 같은 방식은 한 번만 발송됩니다(표식 `data/sent/날짜_슬롯_L30.txt` 등). 수동 실행은 항상 발송됩니다.

## 401 Unauthorized일 때 (토큰 문제)
401은 "GitHub가 호출한 사람을 못 알아봄"입니다. 예전에 422가 나왔다면 그때는 토큰이 통과했다는 뜻이라, 그 뒤에 **토큰이 만료·폐기됐거나 새 작업에 값이 잘못 들어간 것**입니다.
1. GitHub → 계정 Settings → Developer settings → Personal access tokens → Fine-grained tokens 에서 해당 토큰의 **만료일/상태**를 확인합니다. 만료됐으면 **Regenerate token**(또는 새로 생성, 만료는 가능한 길게)으로 새 값을 받습니다.
2. 토큰 권한: Repository access = `market-bot`, Repository permissions → **Actions: Read and write**.
3. 값이 정상인지 한 줄로 확인(`github_pat_...` 부분만 바꿔서 실행):
   - Mac/Linux: `curl -i -H "Authorization: Bearer github_pat_토큰" https://api.github.com/user`
   - Windows: `curl.exe -i -H "Authorization: Bearer github_pat_토큰" https://api.github.com/user`
   - `200`이면 토큰 정상 → cron-job.org 에 값이 잘못 들어간 것. `401 Bad credentials`면 토큰이 만료·폐기·오타.
4. cron-job.org 의 Authorization 값은 **`Bearer ` + 토큰** (Bearer 뒤에 공백 한 칸, 따옴표·줄바꿈·앞뒤 공백 없음). 이름 칸에는 `Authorization` 만 씁니다. 토큰을 중간까지만 복사하지 않았는지 확인합니다.
5. **작업 하나만 먼저 고쳐서 Test run → 204** 를 확인한 뒤, 나머지는 그 작업을 복제(Duplicate)해서 시각과 본문(slot)만 바꿉니다. (같은 오타가 모든 작업에 복사돼 있기 쉽습니다.)
6. 토큰 값을 저장소 파일(README 등)에 붙여넣지 마세요. 공개 저장소에 토큰이 올라가면 GitHub가 **자동으로 폐기**해서 바로 401이 됩니다.
7. 토큰 만료일을 휴대폰 달력에 적어 두세요(만료 2주 전 알림).


---

# 텔레그램으로 받기 (카카오톡 대신 또는 함께)

텔레그램은 카카오톡보다 설정이 간단하고 제약이 적습니다.
- 한 메시지 **4,096자**까지 보낼 수 있어서 요약이 잘리거나 여러 건으로 쪼개지지 않습니다. (카카오는 200자)
- 토큰이 **만료되지 않아** 카카오처럼 60일마다 갱신할 필요가 없습니다.
- 링크 미리보기가 자동으로 붙고, 알림 제한이 적습니다.

## 1) 봇 만들기 (휴대폰 텔레그램 앱, 2분)
1. 검색창에 **@BotFather**(파란 체크 표시가 있는 공식 계정)를 검색해 대화를 시작합니다.
2. `/newbot` 을 보내고 안내에 따라 ① 봇 이름(아무거나), ② 사용자명(영문, 반드시 `bot`으로 끝남, 예: `gyujong_market_bot`)을 입력합니다.
3. 완료 메시지에 나오는 **토큰**(`123456789:AA...` 형태)을 복사해 둡니다. 이것이 `TELEGRAM_BOT_TOKEN` 입니다.

## 2) 내 채팅 ID 알아내기
1. 방금 만든 봇을 검색해 대화창을 열고 **Start**를 누른 뒤 아무 글자나 한 번 보냅니다. (이걸 안 하면 봇이 나에게 메시지를 보낼 수 없습니다)
2. 브라우저 주소창에 아래를 입력합니다. `토큰` 자리에 위 토큰을 넣고, **`bot` 과 토큰 사이에 공백이 없어야 합니다.**
   `https://api.telegram.org/bot토큰/getUpdates`
3. 나오는 글에서 `"chat":{"id":123456789` 처럼 **숫자**를 찾습니다. 이것이 `TELEGRAM_CHAT_ID` 입니다. (비어 있으면 봇에게 글자를 한 번 더 보내고 새로고침)
4. 여러 사람에게 보내려면 각자 봇에서 Start를 누르게 한 뒤 ID를 쉼표로 이어 붙입니다: `111111111,222222222`. 단체방은 봇을 방에 초대하고 방에서 글을 쓴 뒤 같은 방법으로 ID를 찾으면 음수(`-100...`)로 나옵니다.

## 3) GitHub에 등록
저장소 Settings → Secrets and variables → Actions
- **Secrets** 탭 → New repository secret: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- (선택) **Variables** 탭 → New repository variable: `NOTIFY_CHANNELS`
  - `telegram` : 텔레그램만 (이때는 카카오 시크릿이 없어도 되고 카카오 토큰 갱신도 필요 없음)
  - `kakao,telegram` : 둘 다
  - `kakao` : 카카오만
  - 만들지 않으면: 텔레그램 시크릿이 등록돼 있을 때 자동으로 둘 다 보냅니다.
- 워크플로우 `market.yml` 을 새 파일(맨 위 `dispatch-v9`)로 교체해야 시크릿이 전달됩니다. `market_bot.py` 도 함께 교체합니다.

## 4) 시험
1. 먼저 텔레그램 자체가 되는지 (Windows는 `curl.exe`):
   `curl -s "https://api.telegram.org/bot토큰/sendMessage" -d chat_id=내ID -d text=테스트`
   휴대폰에 "테스트"가 오면 성공입니다.
2. 봇 전체 흐름: Actions → market-report v9 → Run workflow → slot `0630`, targets `D`, wait `false` 로 실행하면 몇 분 뒤 D 결과가 텔레그램으로 옵니다.

## 주의
- **봇 토큰은 비밀번호입니다.** 저장소 파일(README 등)이나 채팅에 붙여넣지 마세요. 노출됐다면 @BotFather 에서 `/revoke` 로 폐기하고 새 토큰을 받아 GitHub Secret을 바꿉니다.
- 같은 메시지가 카카오와 텔레그램에 모두 가므로, 한쪽만 쓰려면 `NOTIFY_CHANNELS` 를 지정하세요.


---

# 발송 채널 선택: 카카오+텔레그램 동시 / 텔레그램만 / 카카오만

채널은 저장소 **Variables**의 `NOTIFY_CHANNELS` 값 하나로 정합니다. (Secrets가 아니라 Variables 탭입니다)

| 원하는 방식 | `NOTIFY_CHANNELS` 값 | 필요한 Secrets |
|---|---|---|
| **카카오 + 텔레그램 동시** | `kakao,telegram` (또는 변수를 만들지 않음) | 카카오 시크릿 + `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| **텔레그램만** | `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (카카오 시크릿 불필요) |
| 카카오만 | `kakao` | 카카오 시크릿 |

- 변수를 **만들지 않으면** 텔레그램 시크릿이 등록돼 있을 때 자동으로 동시 발송입니다. 텔레그램 시크릿이 없으면 카카오만 갑니다.
- 값은 소문자로, 두 개는 쉼표로 이어 씁니다. 다른 값(예: `sms`)을 쓰면 아무것도 발송되지 않고 로그에 안내가 나옵니다.

## 변수 만들고 바꾸는 법
1. 저장소 → **Settings** → **Secrets and variables** → **Actions** → **Variables** 탭
2. 처음이면 **New repository variable** → Name `NOTIFY_CHANNELS`, Value 에 위 표의 값 → **Add variable**
3. 바꿀 때는 목록의 `NOTIFY_CHANNELS` 오른쪽 **연필 아이콘**으로 값을 수정합니다.
4. 코드를 다시 올릴 필요 없이 **다음 실행부터** 적용됩니다.

## 텔레그램만 받도록 바꾸는 순서
1. 텔레그램 봇과 Secrets 두 개(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)가 등록돼 있는지 확인합니다. (위 "텔레그램으로 받기" 1~3단계)
2. Variables에 `NOTIFY_CHANNELS` = `telegram` 으로 등록합니다.
3. Actions → market-report v9 → Run workflow (slot `0630`, targets `D`, wait `false`)로 한 번 실행합니다.
4. 텔레그램에 D 결과가 오고, 카카오톡에는 오지 않으면 성공입니다. 실행 로그 맨 위쪽에 `[채널] 사용: telegram` 이 찍힙니다.
5. 카카오 Secrets(`KAKAO_*`, `GH_PAT`)는 지워도 되지만, **다시 카카오를 쓸 수도 있으니 보관을 권합니다.** 텔레그램만 쓰는 동안은 카카오 토큰 갱신(60일)이 필요 없습니다.
6. 호출 방식(cron-job.org)·시각·중복 방지는 채널과 무관하게 그대로입니다.

되돌리려면 `NOTIFY_CHANNELS` 를 `kakao,telegram` 또는 `kakao` 로 바꾸면 됩니다.

## 한쪽이 고장 나도 다른 쪽은 계속 갑니다
동시 발송 중 카카오 토큰이 만료돼도 텔레그램은 정상 발송되고, 텔레그램 메시지 맨 위에 `⚠ 카카오 토큰 오류로 카톡은 발송하지 못했습니다(토큰 갱신 필요)` 라고 알려 줍니다. 텔레그램 서버에 문제가 있어도 카카오는 정상 발송됩니다.


---

# 텔레그램이 안 올 때 (curl 은 되는데 봇 결과만 안 올 때)

**먼저 `telegram-test` 를 실행하세요.** Actions → telegram-test → Run workflow. 로그의 ✔/❌ 문장이 Secrets·토큰·채팅 ID 중 무엇이 문제인지 바로 알려 줍니다. (자세한 순서: `SETUP_FROM_SCRATCH.md` 단계 4)

curl 시험 메시지가 왔다면 **토큰과 채팅 ID는 정상**입니다. 문제는 GitHub의 봇이 그 값을 받아 쓰고 있느냐입니다.

## 1) 실행 로그에서 원인 읽기 (1분)
저장소 **Actions** → 해당 시각의 실행 클릭 → 왼쪽 `run` 클릭 → **Send Kakao…** 단계를 펼칩니다. 화면 위쪽에서 `[채널]` 과 `[텔레그램]` 으로 시작하는 줄을 찾으세요.

| 로그에 보이는 것 | 원인 | 조치 |
|---|---|---|
| `[채널]`·`[텔레그램]` 줄이 **아예 없음** | 저장소의 `market_bot.py` 가 옛 버전 | `market_bot.py` 를 새 파일로 교체 |
| `[채널] 사용: kakao` + `텔레그램 제외됨: … 시크릿이 이 실행에 전달되지 않았습니다` | ① Secrets 미등록·이름 오타·**Variables 탭에 잘못 등록** ② `market.yml` 이 v9 이전 버전 | 아래 2)로 확인, `market.yml` 첫 줄이 `dispatch-v9`인지 확인 |
| `NOTIFY_CHANNELS='kakao' 설정 때문에 텔레그램 제외` | Variables 의 `NOTIFY_CHANNELS` 가 `kakao` 로 돼 있음 | 값을 `kakao,telegram` 으로 수정하거나 변수 삭제 |
| `[텔레그램] 토큰 없음` | `TELEGRAM_BOT_TOKEN` 이 비어 있음 | Secret 다시 등록 |
| `telegram: 400 … chat not found` | 채팅 ID 가 틀림(또는 봇에서 Start 를 안 누름) | 채팅 ID 확인(숫자만, 단체방은 `-100…`) |
| `telegram: 401` | 토큰이 틀림(또는 폐기됨) | 토큰을 다시 복사해 등록 |
| `telegram: 403` | 봇이 차단됐거나 방에서 쫓겨남 | 봇 대화창에서 차단 해제 후 Start |
| `telegram: 200` 인데 폰에 안 옴 | Secret 의 채팅 ID 가 curl 에서 쓴 ID 와 다른 대화방 | curl 에서 쓴 ID 와 Secret 값을 비교 |

## 2) Secrets 가 맞게 등록됐는지 확인
- 저장소 **Settings → Secrets and variables → Actions → Secrets 탭** (Variables 탭이 아닙니다)
- 이름이 **정확히** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (대문자, 밑줄, 앞뒤 공백 없음)
- 값은 따옴표 없이: 토큰은 `숫자:영문숫자…`(가운데 콜론 포함), 채팅 ID 는 숫자만(음수 가능)
- GitHub 는 저장된 값을 보여 주지 않으므로, 의심되면 **연필 아이콘으로 값을 다시 붙여넣어 저장**하세요. 값 끝에 줄바꿈·공백이 섞였는지도 이때 정리됩니다.

## 3) 바로 확인하는 방법
Actions → market-report → Run workflow → slot `0630`, targets `D`, wait `false` 로 실행하고 로그의 `[채널]`, `[텔레그램]` 줄과 텔레그램 수신 여부를 함께 봅니다. 정시를 기다리지 않아 몇 분 안에 끝납니다.


---

# 결과에 들어가는 지수·ETF 설명

## C: 코스피·코스닥 지수 (맨 위)
- C 결과 맨 위에 코스피·코스닥의 현재 지수, 전일 종가, 등락률, 기준 시각이 나옵니다. 메시지에도 `지수 코스피 2,615.50 ▲0.60% / 코스닥 846.20 ▼0.45% (…)` 한 줄이 들어갑니다.
- 출처는 야후 파이낸스 1분봉입니다. **거래소 실시간과 15~20분 차이가 날 수 있습니다.** (무료로 쓸 수 있는 공식 실시간 지수 서비스가 없습니다)
- 상태 표시: 장중이면 `장중 · 약 N분 지연`, 장 마감 후면 `장 마감 기준 날짜 15:30`, 1분봉이 없으면 `일봉 기준`, 장중인데 데이터가 45분 넘게 오래되면 `⚠ 데이터 N분 지연`(휴장일이거나 소스 지연).
- 지수만 조회에 실패해도 C 본문(종목·ETF 표)은 그대로 나오고, 실패 안내만 따로 표시됩니다.
- **1630 슬롯**은 C 전체(코스피 종목, 한국 ETF, 미국투자 한국 ETF)를 장 마감 후 확정 수치로 보냅니다. 상위 50개 표는 메시지가 아니라 리포트 링크에 들어 있고, 메시지에는 요약(상한·하한가, 상승·하락 5개, 52주 신고가, 거래량·거래증가 TOP)이 옵니다.

## B: 미국 ETF 결과 하단의 ETF 설명
- **ETF 설명은 리포트 페이지(HTML) 하단에만** 나옵니다. 이번 목록에 나온 종목(최대 약 200개)의 **정식 이름·유형·한국어 설명** 표가 붙고, 텔레그램·카카오 메시지에는 붙지 않습니다(요약 본문만 갑니다).
- 설명을 만드는 방식
  1. 야후 파이낸스에서 이름·분류·요약을 가져옵니다(처음 본 종목만 조회하고 `data/us_etf_info.json`에 저장해 다음부터는 재사용).
  2. `ANTHROPIC_API_KEY`가 있으면 AI가 한국어 한 줄 설명을 만듭니다.
  3. 키가 없거나 실패하면 **이름 기반 규칙**(레버리지·인버스·커버드콜·채권·원자재 등 유형 표시)으로 대신합니다. 이때는 `(이름 기반)`이라고 표시됩니다.
- 설명은 틀릴 수 있으니 투자 전에 운용사 자료를 확인하세요. 레버리지·인버스 ETF는 하루 단위 수익률을 추종하도록 설계돼 오래 보유하면 오차가 커진다는 안내도 함께 붙습니다.
- 메시지에도 설명을 붙이고 싶으면 저장소 **Variables**에 `ETF_NOTE_CHANNELS` 를 만듭니다(B의 ETF 설명과 A의 업종 ETF 설명 줄에 모두 적용됩니다):
- 선택 설정: `ETF_INFO_DEADLINE_SEC`(기본 150, 설명 만드는 총 시간 상한), `ETF_INFO_THREADS`(기본 6).

## A6: 미국 업종별 ETF 설명
- A6 하단에 SPDR Select Sector 11종(XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC)의 업종·정식 이름·설명 표가 붙고, 11개를 합치면 S&P500 전체에 해당한다는 안내가 나옵니다.
- 메시지는 **결과(강세·약세 업종)만** 보내고, 이 설명은 리포트(HTML)에만 둡니다. 메시지에도 붙이려면 아래 "메시지는 결과 위주"의 `ETF_NOTE_CHANNELS` 를 쓰세요.
- A1·A2·A3만 도는 슬롯에서는 A6이 없으므로 이 설명도 나오지 않습니다(0630, 0830의 A 전체에서만).


---

# HTML 하단의 항목·ETF 설명 (A·C·D·E)

B처럼 **리포트 페이지(HTML) 맨 아래에만** 나오고, 텔레그램·카카오 메시지에는 붙지 않습니다. (메시지는 요약 본문만 갑니다)

| 대상 | 하단에 나오는 설명 |
|---|---|
| **A** (전체) | `표 읽는 법`(기간별 증감·주차·색 표시) + 섹션별 **항목 설명 88개**: A1 환율·주가·M2·P/E·RSI, A2 PMI·산업금속·VIX·공포탐욕·AAII, A3 금리, A4 현물(원유·가스·곡물·금속·운임·대용 ETF), A5 선물, A6 한국 수출입·중국 지표. 기존 **업종별 ETF 11종 설명표**도 그대로 있습니다. |
| **A1 / A2 / A3** (한국 시간대 슬롯) | 그 슬롯에서 실제로 도는 섹션의 설명만(A1이면 A1 설명만) |
| **C** | `용어·기준`(상한·하한가, 52주 신고가, 거래증가율, 지수 지연 등) + **이번 목록에 나온 한국 ETF**의 운용 브랜드·유형 표 |
| **D** | VAA에 쓰는 ETF 7개(SPY·EFA·EEM·AGG·LQD·IEF·SHY)의 정식 이름·설명·역할(공격/안전) + 모멘텀 점수·공격/방어 모드 용어 |
| **E** | 올시즌스 ETF 4개(SPY·TLT·GLD·SHY)의 정식 이름·설명·역할 + 합산 수익률·리밸런싱 용어 |

## 알아둘 점
- **C의 한국 ETF 설명은 이름으로 자동 분류한 것**입니다. KODEX·TIGER·ACE·SOL·RISE 같은 브랜드로 운용사를 알려 주고, 레버리지·인버스·커버드콜·배당·채권·원자재·해외지수·테마·가상자산·자산배분 등 유형을 이름의 단어로 판단합니다. 이름만으로 알 수 없으면 `이름만으로 유형 판단 불가`라고 표시하며, 틀릴 수 있으니 투자 전에 운용사 자료를 확인하세요.
- A·D·E의 설명은 **고정 문구**라 AI 키가 필요 없고, 항목 이름이 바뀌지 않는 한 틀릴 일이 없습니다. 값이 아니라 "이 항목이 무엇인지"를 설명합니다.
- 새 항목을 추가하면 설명도 `A_GLOSS`(코드 안 표)에 한 줄 추가해야 하며, 이름이 앞부분만 같아도(`한 국고채3년`처럼) 설명이 연결됩니다.
- B의 ETF 설명은 `ETF_NOTE_CHANNELS` 설정으로 메시지에도 붙일 수 있지만, A·C·D·E의 이 설명은 **HTML 전용**이라 메시지로 보내는 설정은 없습니다. 필요하면 요청해 주세요.


---

# 메시지는 결과 위주, 설명은 HTML에 (A·B·C·D·E 모두)

**텔레그램·카카오 메시지에는 결과만** 가고, 항목·ETF·용어 설명은 **리포트 페이지(HTML) 하단**에만 나옵니다.

| 대상 | 메시지에서 빠진 것 | HTML 하단에 있는 것 |
|---|---|---|
| A | 업종 ETF 안내·설명 줄, "전체 항목은 리포트 링크에서 확인" 안내 | 표 읽는 법, 항목 설명 88개, 업종 ETF 11종 표 |
| B | `─ ETF 설명 ─` 구간 | ETF 정식 이름·유형·한국어 설명 표 |
| C | (원래 설명 줄 없음) | 용어·기준, 한국 ETF 브랜드·유형 표 |
| D | (원래 설명 줄 없음) | VAA ETF 7개 설명, 모멘텀·모드 용어 |
| E | (원래 설명 줄 없음) | 올시즌스 ETF 4개 설명, 수익률·리밸런싱 용어 |

- 메시지 맨 아래 링크 문구는 `전체 리포트(그래프·표·항목 설명)` 입니다. 설명은 그 링크에서 봅니다.
- 결과 줄(등락·RSI·섹터 강세/약세·조회불가 개수·신호 등)은 그대로입니다.
- **메시지에 설명을 되살리려면** 저장소 Variables 에 `ETF_NOTE_CHANNELS` 를 만듭니다. `telegram`(텔레그램만) / `kakao`(카카오만) / `kakao,telegram`(둘 다). 비우거나 만들지 않으면 둘 다 결과만 갑니다. 이 변수를 전달하려면 `market.yml` 에 `ETF_NOTE_CHANNELS: ${{ vars.ETF_NOTE_CHANNELS }}` 줄이 있어야 합니다(이미 들어 있는 최신 파일이면 필요 없음).

# C: 거래대금 증가 상위 30 (각 묶음의 마지막 표)
- C1 코스피 종목, C1 한국 ETF, C2 미국증시 투자 한국 ETF **각각의 마지막**에 `거래대금 증가 상위 (전일 대비 증가액 · 매수+매도 합산)` 표(30행)가 붙습니다. 열: 종목, 종가, 등락률, 거래대금(억), 전일 거래대금(억), 증가액(억), 증가율(%).
- **"매수거래 증가"는 일봉 데이터로 매수와 매도를 구분할 수 없어서**, 매수·매도를 합친 **거래대금이 전일보다 금액으로 가장 많이 늘어난 순서**로 보여 드립니다. 외국인·기관 순매수와는 다릅니다.
- 제외 규칙: 거래대금이 1억 원 미만이거나 전일 거래대금이 0인 종목.
- 메시지에는 묶음마다 `거래대금증가TOP3: 종목 +증가액억` 한 줄이 들어갑니다.
