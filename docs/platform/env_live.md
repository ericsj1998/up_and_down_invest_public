# `.env.live` — 실계좌 서버 환경변수의 단일 목록 (2026-09-06)

> 서버 `~/updown/.env.live` 에 **무엇이 있어야 하고 무엇은 없어야 하나**. 값은 이 문서에도, 로그에도, 채팅에도 적지 않는다 —
> **이름·유무·출처**만 다룬다. 값을 바꾸는 손은 사람의 것이다 ([ops_runbook.md §4](ops_runbook.md)). 이 목록의 근거는
> 코드가 실제로 읽는 이름이다: `Settings`(`common/config.py`) · `os.environ` 직접 읽기(`provider.py` · `gateway.py`) ·
> compose `${VAR}` · Caddy `{$VAR}`. 여기 없는 이름은 서버가 **안 읽는다**.

## 1. 살아 있어야 하는 변수 (26개)

| 변수 | 누가 읽나 | 값의 출처 | 지금 서버 |
|---|---|---|---|
| `APP_ENV` | Settings · compose | `live` 고정 | ✅ |
| `LIVE_ORDERS` | Settings → `OrderGateway` | `1` 실계좌 · `0` 이면 같은 코드가 테스트넷 | ✅ |
| `STOCK_LIVE_ORDERS` | Settings(`stock_live_orders`) → 주식 주문 경로 | **줄 없음 = 0**(페이퍼). `1` 로 켜면 실주문 어댑터가 없어 **즉시 예외**(T240 관문) — 토스 실주문 어댑터가 생기기 전엔 켜지 않는다 | ✅ 줄 없음 |
| `RUN_START_REQUEST_CAP` | Settings(`run_start_request_cap`) → 판 시작 워밍업(`seed_within_budget`) | **줄 없음 = 300**. 폴링 브로커(토스)만 센다 · 넘으면 판을 안 띄우고 503(T253) · 0 = 무제한 | ✅ 줄 없음 |
| `EDGAR_USER_AGENT` | Settings(`edgar_user_agent`) → `EdgarAdapter` | `앱이름 이메일`(ASCII 만 — 한글은 헤더 인코딩에서 500). 실제 UA 는 `Mozilla/5.0` 이고 이메일은 `From` 헤더로 간다(Akamai 가 선언 UA 를 403 · T243 실측 2026-09-10). 없으면 재무 갱신이 즉시 실패하고 조회는 빈 표 | ⬜ 서버에 없음 — **사용자가 넣는다**. 값을 안 찍고 옮기는 스크립트는 있다: `bash scripts/ops/env_push_ai.sh`(연구 PC `.env.dev` → 서버 `.env.live`·`.env.demo` · `NVIDIA_LLM_ACCESS_KEY` 도 함께) → `bash scripts/ops/remote.sh scripts/ops/swap_same_image.sh`(같은 이미지 블루그린 · env 재적용). 에이전트는 권한 분류기가 막아 **사람이 돌린다**(2026-09-11 실측) |
| `UPDOWN_MARKETS` | `provider.live_markets` | `GATE` (배포는 Gate 만 · Binance 경고 폭주 차단) | ✅ |
| `GATE_API_KEY` · `GATE_API_SECRET` | Settings → `GateLiveAdapter` | **live 전용** 실키 (선물 R/W · Account Read · 출금 OFF · IP 화이트리스트) | ✅ |
| `GATE_TESTNET_API_KEY` · `GATE_TESTNET_API_SECRET` | `gateway.py` · `walkforward.py` | dev 와 **같은 값** — `LIVE_ORDERS=0` 으로 내릴 때 쓴다 · 화이트리스트에 서버 IP 필요 | ✅ |
| `POSTGRES_USER` · `POSTGRES_PASSWORD` · `POSTGRES_DB` | compose(postgres · backup · migrate) | **서버 전용** 새 비밀 (dev 와 다르다) | ✅ |
| `DATABASE_URL` | Settings · alembic | `postgresql+psycopg://<user>:<pw>@postgres:5432/<db>` — 위 셋과 같은 값 | ✅ |
| `REDIS_URL` | Settings | `redis://redis:6379/0` | ✅ |
| `LOG_LEVEL` | Settings · compose | `INFO` | ✅ |
| `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET` | Settings(auth) | dev 와 **같은 OAuth 클라이언트** (콘솔에 배포 콜백이 등록돼 있어야 한다) | ✅ |
| `GOOGLE_REDIRECT_URI` | Settings(auth) | `https://<PUBLIC_DOMAIN>/api/auth/callback` — dev 와 **다르다** | ✅ |
| `SESSION_SECRET` | Settings(auth) | **서버 전용** (`openssl rand -base64 48`) · 데모 API(`.env.demo`)와는 같은 값 | ✅ |
| `ADMIN_EMAILS` | Settings(auth) | 첫 로그인에 admin 이 될 이메일 · dev 와 같아도 된다 | ✅ |
| `SMTP_HOST` · `SMTP_PORT` · `SMTP_USER` · `SMTP_PASSWORD` · `NOTIFY_FROM_EMAIL` | Settings → `report/daily.py` | dev 와 **같은 값** (같은 메일 계정) | ✅ (2026-09-06 `env_sync.sh` 로 채움 — 그 전엔 비어 있었고 `SMTP_PORT` 는 줄이 없었다) |
| `REPORT_TO` | Settings → 일간 리포트 수신자 | 받을 주소 | ✅ |
| `REPORT_HOUR_KST` | Settings(`report_hour_kst` · 기본 9) | 정수 시각. 09시면 줄이 없어도 된다 | ✅ 줄 없음(기본 9). 죽은 이름 `REPORT_AT_KST` 는 지웠다(§3) |
| `PUBLIC_DOMAIN` · `ACME_EMAIL` | compose.proxy → Caddy | 도메인 · 인증서 만료 안내 주소 (`:?` 필수) | ✅ |
| `IMAGE_TAG` | compose.live(이미지 태그) | `ship.sh` 가 배포마다 바꾼다 — **사람이 만지지 않는다** | ✅ |
| `BACKUP_KEEP_DAYS` | compose(backup) | `7` | ✅ |

`UPDOWN_ENGINE_INPROC=1` 은 `compose.live.yml` 이 리터럴로 박는다 — env 파일에 둘 필요 없다.

## 2. 지운 변수 (서버에 있었지만 아무도 안 읽었다 · 2026-09-06 제거)

| 변수 | 왜 지우나 |
|---|---|
| `UPBIT_ACCESS_KEY` · `UPBIT_SECRET_KEY` | 배포는 `UPDOWN_MARKETS=GATE` — 업비트는 호출 자체가 없고, 있어도 공개 API 조회라 키가 필요 없다. 비어 있는 채로 남으면 "설정해야 하나" 하는 오해만 남긴다 |
| `REPORT_AT_KST` | 코드가 읽는 이름은 `REPORT_HOUR_KST`(정수) 다. `.env.example` 이 낡은 이름을 실어 왔다(§3 에서 고쳤다). 09시면 아예 없어도 된다 |

⛔ 다음은 **`.env.live` 에 있으면 안 되는** 이름이다(§12.4 격리 — 반대편 파일에 라이브 키가 있으면 기동 거부):
`.env.demo` 에 `GATE_API_*` · `.env.live` 에 `BINANCE_TESTNET_*`(배포는 Binance 를 안 쓴다) · `TOSS_*` · `DART_API_KEY` · `NVIDIA_LLM_ACCESS_KEY`(연구 PC 전용) · `AUTH_TEST_BYPASS` · `WALK_SYNTH_DIR`(테스트 문 · 라이브 금지).

## 3. 이번에 드러난 어긋남

- `.env.example` · `.env.dev` · `.env.live` 모두 `REPORT_AT_KST=09:00` 을 갖고 있었는데 `Settings` 는 `report_hour_kst: int = 9` 를 읽는다 —
  **셋 다 죽은 줄**이었고 09시로 돌아간 것은 기본값 덕이다. `.env.example` 을 `REPORT_HOUR_KST=9` 로 고쳤다. 다른 시각을 원하면
  그 이름으로 정수를 적는다.
- `HEARTBEAT_URL` 은 `.env.example` 에만 있고 **읽는 코드가 없다**(P2-5 데드맨 · 미구현). 2026-09-06 사용자 결정으로 **보류** —
  `.env.live` 에 두지 않는다.
- `SMTP_PORT` 는 서버 파일에 줄이 없었다(기본 587). 이제 dev 와 같은 값이 명시돼 있다.

## 4. 채우는 법 — dev 기준으로

비어 있는 다섯 줄은 전부 **연구 PC 의 `.env.dev` 와 같은 값**이다 (같은 메일 계정으로 보낸다). 연구 PC(WSL)에서:

```bash
cd ~/projects/up_and_down_invest
grep -E '^(SMTP_HOST|SMTP_PORT|SMTP_USER|SMTP_PASSWORD|NOTIFY_FROM_EMAIL)=' .env.dev
```

이 다섯 줄을 그대로 복사해 서버 `~/updown/.env.live` 의 같은 이름 줄에 붙인다(`SMTP_PORT` 는 줄을 새로 만든다). 그 외에는
채울 것이 없다 — `GOOGLE_*` · `SESSION_SECRET` · `ADMIN_EMAILS` · Gate 키는 이미 들어 있다.

## 5. 정리·적용 절차

**한 줄 (2026-09-06 적용 완료)**: `bash scripts/ops/env_sync.sh` — 연구 PC `.env.dev` 의 SMTP 다섯 줄을 서버로 옮기고, `.env.live` 를
§1 목록으로 정리하고(백업 `.env.live.bak-<시각>`), `.env.demo` 가 없으면 `.env.live` 값으로 만든다(라이브 키 제외 · 격리 자가 검사).
값은 어디에도 찍지 않는다. **컨테이너는 재생성하지 않는다** — 다음 배포의 새 슬롯이 이 env 로 뜬다. 배포 전에 메일을 바로
쓰려면 아래 3 의 재생성 한 줄만 사람이 돌린다. 2026-09-06 01:25 KST 실행 결과: `.env.live` 28개 · 빈 이름 0 · `.env.demo` 8개.

아래는 손으로 할 때의 절차(같은 일):

1. 연구 PC 에서 제안 파일을 만든다 — 값은 건드리지 않고 **§1 의 이름만 남기고 §2 를 지우고, 빠진 이름을 빈 줄로 덧붙인** 사본이다:

   ```bash
   bash scripts/ops/remote.sh scripts/ops/env_live_proposed.sh     # 서버에 ~/updown/.env.live.proposed 를 쓰고 이름 diff 만 출력
   ```

2. 서버에 들어가 사본을 확인하고 바꾼다:

   ```bash
   ssh -i ~/.ssh/lightsail-tokyo.pem ubuntu@<서버 IP>
   cd ~/updown
   diff <(grep -oE '^[A-Z_]+' .env.live | sort) <(grep -oE '^[A-Z_]+' .env.live.proposed | sort)   # 이름만
   cp .env.live .env.live.bak-$(date +%Y%m%d-%H%M) && mv .env.live.proposed .env.live
   nano .env.live        # §4 의 SMTP 다섯 줄 채우기
   chmod 600 .env.live .env.live.bak-*
   ```

3. env 는 컨테이너를 **다시 만들 때** 읽힌다. 리더 슬롯(`api` 또는 `api_b` · `status.sh` 가 말한다)과 `backup` 을 재생성한다:

   ```bash
   docker compose --env-file .env.live -f docker/compose.base.yml -f docker/compose.live.yml -f docker/compose.proxy.yml up -d --no-deps --wait api backup
   ```

   (리더가 `api_b` 면 `api` 대신 `api_b`.) 재생성 중 몇 초는 팔로워가 조회를 받는다 — 거래 리더는 락으로 이어진다.

4. 연구 PC 에는 사본을 두지 않는다 — 2026-09-06 에 `~/.updown_server.env`·로컬 `.env.live`·`.env.paper` 를 지웠다. 단일 출처는 서버 파일이고
   백업은 Lightsail 스냅샷이다 (`ship.sh` 는 `IMAGE_TAG` 한 줄만 바꾼다).
5. 확인: `bash scripts/ops/remote.sh scripts/ops/status.sh` · 관리자 화면 리포트 카드에서 **미리보기 → 보내기** 로 메일 한 통.

## 변경 이력

- 2026-09-06 — 첫 작성. 서버 실측(이름 30 · 빈 6) · `REPORT_AT_KST` 죽은 이름 발견 · 데드맨 보류 · `env_sync.sh` 적용(28 · 빈 0) ·
  연구 PC 의 낡은 사본 셋(`.env.paper` · 로컬 `.env.live` · `~/.updown_server.env`)과 `compose.paper.yml` 삭제.
