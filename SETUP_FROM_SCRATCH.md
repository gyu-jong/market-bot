# 처음부터 다시 설정하기 (텔레그램 중심, 단계별 확인 포함)

이 가이드는 **각 단계마다 "✔ 확인" 항목**이 있습니다. 확인이 안 되면 다음 단계로 가지 말고 그 단계에서 멈춰서 고치세요.
텔레그램이 안 오는 문제의 대부분은 ① 파일 경로·내용이 다르거나, ② Secrets 를 엉뚱한 곳에 등록한 것입니다.

## 올려야 할 파일 5개
| 저장소 안의 위치 | 역할 |
|---|---|
| `market_bot.py` | 본 프로그램 |
| `requirements.txt` | 필요한 패키지 목록 |
| `.github/workflows/market.yml` | 봇 실행 설정 (맨 위 첫 줄이 `# 파일 버전: dispatch-v9`) |
| `.github/workflows/telegram-test.yml` | **텔레그램 연결 점검용** (맨 위 첫 줄이 `# 파일 버전: telegram-test-v1`) |
| `README.md` | 설명서 (선택) |

---

## 단계 0. 깨끗하게 시작하기 (권장)
이미 시도하다 꼬였을 수 있으니, 텔레그램 관련 값을 모두 새로 만듭니다.
1. 저장소 Settings → Secrets and variables → Actions → **Secrets** 탭에서 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 가 있으면 휴지통으로 **삭제**합니다.
2. **Variables** 탭에 `NOTIFY_CHANNELS` 가 있으면 일단 **삭제**합니다. (나중에 정합니다)
3. 텔레그램에서 @BotFather 에게 `/mybots` → 예전 봇 선택 → API Token → **Revoke current token** 으로 옛 토큰을 폐기하거나, 아예 새 봇을 만듭니다(다음 단계).

---

## 단계 1. 텔레그램 봇 새로 만들기 (휴대폰 앱)

### 1-1. 봇 만들기
1. 텔레그램 검색창에 `BotFather` 를 입력하고 **파란 체크 표시가 있는 공식 계정** @BotFather 를 엽니다.
2. `/newbot` 을 보냅니다.
3. 봇 **이름**을 묻습니다 → 아무 이름 (예: `규종 시장봇`).
4. 봇 **사용자명**을 묻습니다 → 영문·숫자·밑줄만, **반드시 `bot` 으로 끝남** (예: `gyujong_market_bot`). 이미 쓰는 이름이면 다른 이름으로 다시.
5. 완료 메시지에 **토큰**이 나옵니다. `숫자:영문숫자…` 모양입니다 (예: `123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`).
6. 이 토큰을 **메모장에 복사**해 둡니다. (비밀번호와 같으니 채팅·저장소 파일에 붙여넣지 마세요)

### 1-2. 봇과 대화 시작하기 (이걸 빼먹으면 봇이 나에게 메시지를 못 보냅니다)
1. 1-1 의 완료 메시지에 있는 링크(`t.me/봇이름`)를 누르거나, 검색창에 봇 사용자명을 입력해 엽니다.
2. 아래의 **START(시작)** 버튼을 누릅니다.
3. 아무 글자(`hi`)나 한 번 보냅니다.

### 1-3. 내 채팅 ID 알아내기 (둘 중 편한 방법)
**방법 A (봇으로 확인)**
1. 컴퓨터/휴대폰 브라우저 주소창에 아래를 입력합니다. `토큰` 자리에 1-1 의 토큰을 넣습니다. **`bot` 과 토큰 사이에 공백이 없어야 합니다.**
   `https://api.telegram.org/bot토큰/getUpdates`
2. 나오는 글에서 `"chat":{"id":` 바로 뒤의 **숫자**를 찾습니다. (예: `"chat":{"id":123456789,`)
3. 글이 `{"ok":true,"result":[]}` 처럼 비어 있으면 1-2 의 Start 와 `hi` 를 다시 하고 새로고침합니다.

**방법 B (ID 알려 주는 봇)**
1. 텔레그램에서 `@userinfobot` 을 열고 Start 를 누르면 `Id: 123456789` 처럼 알려 줍니다.

> 단체방으로 받으려면: 봇을 방에 초대하고 방에서 아무 글자를 쓴 뒤 방법 A 를 합니다. 방의 ID 는 `-100…` 처럼 **음수**로 나옵니다.

### ✔ 확인 1: 브라우저로 직접 메시지 보내 보기
브라우저 주소창에 아래를 입력하고 엔터를 칩니다. (`토큰`, `내ID` 교체)
`https://api.telegram.org/bot토큰/sendMessage?chat_id=내ID&text=테스트`
- **휴대폰에 "테스트"가 오면 성공**입니다. (브라우저 화면에는 `"ok":true` 가 보입니다)
- `chat not found` → 채팅 ID 가 틀렸거나 1-2 의 Start 를 안 눌렀습니다.
- `Unauthorized` → 토큰이 틀렸습니다.
- 여기서 성공해야만 다음 단계로 갑니다.

---

## 단계 2. 파일 올리기 (GitHub 웹)
저장소 `gyu-jong/market-bot` 에서 진행합니다.

### 2-1. 워크플로우 2개 (`.github/workflows/` 아래)
1. 저장소 첫 화면 → **Add file → Create new file**
2. 맨 위 **파일 이름 칸**에 정확히 입력: `.github/workflows/market.yml`
   (슬래시 `/` 를 입력하면 폴더가 자동으로 만들어집니다. 이미 있는 파일이면 그 파일을 열어 연필 아이콘으로 **내용을 전부 지우고** 붙여넣습니다.)
3. 본문 칸에 `market.yml` 의 **전체 내용**을 붙여넣습니다.
4. 오른쪽 위/아래의 **Commit changes** → **Commit changes** 를 누릅니다.
5. 같은 방법으로 `.github/workflows/telegram-test.yml` 도 만듭니다.

### 2-2. 일반 파일 3개
1. **Add file → Upload files** 를 누르고 `market_bot.py`, `requirements.txt`, `README.md` 를 끌어다 놓은 뒤 **Commit changes**.
   (같은 이름의 파일은 새 파일로 덮어씌워집니다.)

### ✔ 확인 2
- 저장소의 **Actions** 탭 왼쪽 목록에 **`market-report v9`** 와 **`telegram-test`** 두 개가 보여야 합니다.
- 보이지 않거나 빨간 "Invalid workflow file" 표시가 있으면 붙여넣기 중 들여쓰기가 깨진 것입니다. 해당 파일을 열어 내용을 지우고 **처음부터 다시 붙여넣으세요.**
- `.github` 앞의 점을 빼먹거나 `workflows` 철자가 다르면 목록에 나타나지 않습니다.

---

## 단계 3. GitHub Secrets 등록 (여기서 가장 많이 틀립니다)

1. 저장소 위쪽 탭 오른쪽 끝 **Settings**
2. 왼쪽 메뉴 **Security** 구역의 **Secrets and variables** → **Actions**
3. 위쪽 탭이 **Secrets** 인지 확인 (Variables 가 아님)
4. **Repository secrets** 구역의 **New repository secret** 을 누릅니다.
5. 아래 2개를 하나씩 등록합니다.

| Name (대문자·밑줄 정확히) | Secret (값) |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 단계 1-1 의 토큰 (`숫자:영문숫자…`) |
| `TELEGRAM_CHAT_ID` | 단계 1-3 의 숫자 ID (여러 명이면 쉼표로: `111,222`) |

### 흔한 실수
| 실수 | 증상 |
|---|---|
| **Variables** 탭에 등록 | 봇이 값을 못 받음 (`등록 안 됨`) |
| `Dependabot` / `Codespaces` 쪽 Secrets 에 등록 | 봇이 값을 못 받음 (왼쪽 메뉴에서 **Actions** 를 골라야 합니다) |
| **Environment secrets** 구역에 등록 | 봇이 값을 못 받음 (**Repository secrets** 에 등록) |
| 이름 오타 (`TELEGRAM_TOKEN`, 소문자, 공백) | 봇이 값을 못 받음 |
| 값 앞뒤 공백·줄바꿈, 토큰을 중간까지만 복사 | 401 / 형식 오류 |
| 채팅 ID 에 따옴표를 붙임 | chat not found |

값은 한 번 저장하면 다시 볼 수 없습니다. **의심되면 연필 아이콘으로 값을 다시 붙여넣어 저장**하세요.

---

## 단계 4. 텔레그램 연결 점검 (telegram-test)
1. **Actions** 탭 → 왼쪽 **telegram-test** → 오른쪽 **Run workflow** → 초록 **Run workflow** 버튼.
2. 잠시 뒤 새 실행이 나타나면 클릭 → 왼쪽 **test** → **텔레그램 연결 점검** 단계를 펼칩니다.
3. 로그의 **✔ / ❌** 문장을 읽습니다.

### ✔ 확인 3: 아래처럼 나오면 성공
```
✔ TELEGRAM_BOT_TOKEN 등록됨 (길이 46자)
✔ TELEGRAM_CHAT_ID 등록됨
✔ 토큰 정상: 봇 @gyujong_market_bot
✔ 채팅 123… 전송 성공 → 휴대폰 텔레그램에서 메시지를 확인하세요
결과: 모두 정상입니다. ...
```
그리고 휴대폰에 "✅ 텔레그램 연결 시험 성공" 메시지가 옵니다.

### ❌ 가 나오면
| 로그 문장 | 원인 | 고칠 곳 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN 없음` / `TELEGRAM_CHAT_ID 없음` | Secrets 에 안 보임 | 단계 3 의 위치·이름 확인 |
| `토큰 형식 … ':' 가 없습니다` | 토큰을 잘못 복사 | 토큰 전체를 다시 복사해 Secret 새로 입력 |
| `공백·줄바꿈이 섞였습니다` | 값 앞뒤 공백 | Secret 새로 입력 |
| `토큰이 틀렸거나 폐기된 토큰(401)` | 토큰 오류 | BotFather 에서 토큰 재확인 후 새로 입력 |
| `'chat not found'` | 채팅 ID 오류 또는 Start 미클릭 | 단계 1-2, 1-3 다시 |
| `403` | 봇 차단 | 봇 대화창에서 차단 해제 후 Start |

**이 점검이 ✔ 로 끝나기 전에는 본 봇을 시험하지 마세요.** 이 점검이 성공하면 Secrets·토큰·채팅 ID 는 모두 정상입니다.

---

## 단계 5. 본 봇으로 시험
1. Actions → **market-report v9** → Run workflow
   - `slot`: `0630`
   - `targets`: `D`
   - `wait`: **`false`** (즉시 발송)
   - 나머지는 그대로
2. 실행이 끝나면(몇 분) 실행 로그를 엽니다. 맨 위 **Fired + 시크릿 등록 여부** 단계에서:
   ```
   TELEGRAM_BOT_TOKEN: 등록됨
   TELEGRAM_CHAT_ID: 등록됨
   ```
3. 맨 아래 **Send Kakao** 단계에서:
   ```
   [채널] 사용: kakao, telegram
   [텔레그램] 토큰 등록됨 / 채팅ID 1개
   telegram: 200
   ```
4. 휴대폰 텔레그램에 D 결과가 오면 성공입니다.

### ✔ 확인 4 (문제별 의미)
| 로그 | 의미 |
|---|---|
| 시크릿 등록 여부가 `없음` | 단계 3 문제 (telegram-test 는 ✔ 였는데 여기만 없음이면 `market.yml` 이 v9 가 아닌 옛 파일) |
| `[채널] 사용: kakao` + `텔레그램 제외됨…` | `market.yml` 이 옛 파일이거나 위 시크릿이 없음 |
| `NOTIFY_CHANNELS='kakao' 설정 때문에…` | Variables 의 `NOTIFY_CHANNELS` 값을 수정 |
| `[채널]` 줄이 없음 | `market_bot.py` 가 옛 파일 |

---

## 단계 6. 어떻게 받을지 정하기
저장소 Settings → Secrets and variables → Actions → **Variables** 탭 → New repository variable → Name `NOTIFY_CHANNELS`

| 원하는 방식 | 값 |
|---|---|
| 카카오 + 텔레그램 동시 | `kakao,telegram` (또는 변수를 만들지 않음) |
| **텔레그램만** | `telegram` |
| 카카오만 | `kakao` |

다음 실행부터 적용됩니다. 텔레그램만 쓰면 카카오 토큰 갱신(60일)이 필요 없습니다.

---

## 단계 7. 자동 실행 연결 (cron-job.org)
README 의 "정시 실행 설정" 과 "401 Unauthorized일 때" 를 따릅니다. 요약:
- URL: `https://api.github.com/repos/gyu-jong/market-bot/actions/workflows/market.yml/dispatches`
- 방식 POST, 헤더 4개(`Accept`, `Authorization: Bearer 토큰`, `X-GitHub-Api-Version`, `Content-Type: application/json`)
- 본문: `{"ref":"main","inputs":{"slot":"0630@30"}}` (슬롯과 `@30`/`@0` 만 바꿈)

---

## 단계 8. 그래도 안 될 때 보내 주실 것
아래 두 가지를 그대로 복사해 보내 주시면 원인을 바로 짚을 수 있습니다. (토큰·ID 값은 로그에 찍히지 않으니 그대로 복사해도 안전합니다)
1. **telegram-test** 실행 로그의 "텔레그램 연결 점검" 단계 전체
2. **market-report v9** 실행 로그 맨 위 "Fired + 시크릿 등록 여부" 단계와 맨 아래 "Send Kakao" 단계

## 보안 주의
- 봇 토큰은 비밀번호입니다. 저장소 파일, README, 채팅에 붙여넣지 마세요. (공개 저장소에 올리면 누구나 봇을 조종할 수 있습니다)
- 노출됐다면 BotFather 의 `/revoke` 로 즉시 폐기하고 새 토큰을 Secrets 에 다시 등록하세요.
