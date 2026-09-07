# 아키텍처 · 보안 점검 — 추상화 · 하드코딩 · 책임 분리 · 보안

> 2026-09-06 스냅샷. 방법: 정적 스캔(grep · AST · ruff · pyright · import-linter) + 오늘 실제로 잡힌 결함 + 설정 파일 검토.
> 코드 품질 전반은 [code_quality_review.md](code_quality_review.md) 에 있고, 이 문서는 사용자가 따로 물은 네 가지에 답한다.

| 질문 | 답 |
|---|---|
| **필요한 곳에 추상화가 되어 있나** | 돈이 오가는 경계(거래소 · 주문 게이트 · 판정 코어)는 인터페이스 뒤에 있다. 화면 쪽 API 층에서 거래소 이름이 새고 있고, 오늘 그 결함 둘을 고쳤다 |
| **하드코딩이 과하지 않나** | 리스크 임계값은 설정(YAML)에 있어 규약을 지킨다. 거래소 기본값("GATE") 30곳 · 환경변수 직접 읽기 22곳 · 운영 상수(폴링·재시도)는 코드에 있다 |
| **책임 분리가 되어 있나** | 패키지 수준은 계약으로 강제되어 우수. 파일 수준에서 3개 거대 모듈이 여러 이유로 바뀐다 |
| **보안은 어떤가** | 실주문 이중 게이트 · 데모 프로세스 분리 · 화이트리스트 빌드 컨텍스트 · 비루트 컨테이너 · 추가 전용 감사 로그. 미비: 인증 실패 회로차단 · 키 회전 절차 · 의존성 취약점 자동 점검 |

---

## 1. 추상화 — 어디에 있고 어디에 없나

### 1.1 있는 것

| 경계 | 추상화 | 강제 수단 |
|---|---|---|
| 거래소 | `BrokerAdapter` · `QuoteAdapter` + 능력 Protocol 7종(`StopAware` · `PositionAware` · `OrdersAware` …) | 러너는 `isinstance` 로 능력을 묻고 없으면 그 기능을 건너뛴다 |
| 실주문 획득 | `OrderGateway.resolve_adapter` — 파일 하나 | AST 정적 검사(`test_gateway_bypass.py`)가 다른 파일의 구체 어댑터 import 를 잡는다 |
| 실주문 판정 | `common/security/live_gate.py` 순수 함수 (환경 × 사용자 토글 진리표) | 4케이스 시험 |
| 판정 코어 | `Session.step()` 동기·순수 · 봉/체결은 `LiveFeed`·`LiveFiller` 우편함 | 백테스트와 라이브가 같은 함수를 돈다 |
| 셋업 탐지 | `SetupDetector` 프로토콜 + 레지스트리 (새 개념 = 파일 1 + 1줄) | `config/rules/*.yml` 로 매개변수 주입 |
| 대조 · 복원 · 귀속 | `reconcile.compare` · `pending.plan_restore` · `attribute_closes` 순수 함수 | 거래소 없이 표로 시험 |
| 시장 목록 | `MarketDataProvider.live_markets()` 에서 파생 · `UPDOWN_MARKETS` 로 좁힘 | 화면 선택창·리포트·대조가 한 출처를 본다 |
| 임계값 | `config/risk.yml` · `costs.yml` · `playbooks.yml` · `baskets.yml` · `candle_integrity.yml` | 코드에 배수·문턱을 박지 않는 규약(§4.3.1) |
| 저장 | `RunStore` — 판·원장·주문·캘리브레이션의 유일한 쓰기 지점 | 러너는 `use_store` 로 주입받는다 |

### 1.2 새는 곳 (오늘 실측)

| 위치 | 무엇 | 결과 |
|---|---|---|
| `apps/api/exchange.py` — `_orders_adapter(market="GATE")` · `_instrument(symbol, market="GATE")` · `_TRADABLE` 이 Gate 비용표만 · `ranking()` Gate 전용 | 거래소 기본값이 인자에 박혀 호출부가 빠뜨려도 컴파일된다 | **BINANCE 판이 Gate 호가창·계약 명세로 유동성·수량 검사를 받았다** (09-06 수정: `market=` 관통) |
| `apps/api/walkforward.py` 기동 되살리기 | `GATE_TESTNET_API_KEY` 하나에 묶임 | Gate 키가 없으면 다른 거래소 판까지 관리 밖 (09-06 수정: 자격증명 아무 하나) |
| 화면 `ConsoleTab` | 거래소 목록에 "GATE" 하드코딩 | 키 없는 로컬에서 503 배너 (09-06 수정: 서버가 준 목록 + "키 설정 필요" 카드) |
| `"GATE"` 리터럴 30곳 — `exchange.py` 14 · `rebalancer.py` 7 · `trade_client.py` 3 · 기타 6 | 거래소 이름이 문자열로 흩어져 있다 | 새 거래소를 붙일 때 grep 으로 찾아야 한다 |

**판정**: 도메인 경계(집행·판정·저장)의 추상화는 충분하고 시험으로 지켜진다. **얇은 곳은 API 층**이다 — 콘솔용 헬퍼가 "지금은 Gate 만" 이던 시절의 기본값을 들고 있다. 권장: 기본 인자 `"GATE"` 전면 제거(호출부가 반드시 넘기게), `Market` 열거형을 문자열 대신 인자 타입으로.

---

## 2. 하드코딩 — 스캔 결과

| 항목 | 수 | 어디 | 판정 |
|---|---|---|---|
| 외부 URL 리터럴 | 16 (호스트 10종) | 어댑터 base URL(Gate · Binance · Upbit · Toss · NVIDIA · Google OAuth) · `api.ipify.org`(IP 확인) | 어댑터 안의 base URL 은 정당하다. `https://fapi.binance.com` 이 **4곳**에 중복 — 상수 하나로 |
| 거래소 이름 리터럴 `"GATE"` | 30 | §1.2 | 과함 — 기본 인자 제거 대상 |
| `os.environ` 직접 읽기 | 22곳 · 14 파일 · 변수 14종 | `UPDOWN_MARKETS` · `AUTO_LIVE` · `GATE/BINANCE_TESTNET_API_*` · `FUNDS_ROOT` · `WALK_SYNTH_DIR` · `LABEL_ROOT` · `RECONCILE_ROOT` … | `common/config.Settings` 가 있는데 절반만 거친다. 시험에서 monkeypatch 지점이 흩어진다 |
| 운영 상수 | 다수 | `POLL_MS`(화면) · `RETRY_EVERY`(펀드 재시도) · `REVIVE_LIMIT`/백오프 · `CACHE_S` · `STOP_GUARD_LIMIT` | 리스크 값은 YAML 인데 운영 값은 코드. 바꿀 일이 드물어 당장 해롭지는 않다 |
| 수치 리터럴(`apps/api`, 3자리 이상) | 약 790 | HTTP 상태 · 시간(초·ms) · 한도 | 대부분 상태 코드·단위 환산. 의미 있는 값은 이름 붙은 상수로 되어 있다 |
| 서버 도메인 | 0 (dev nginx 거울 설정에 있었으나 걷어냄) | — | ✓ |
| 시크릿 | 0 (`.env*` gitignore · `*.pem` gitignore · 프로브는 이름·유무만) | — | ✓ · 오늘 저장소 루트에 있던 pem 사본 삭제 |

**판정**: "임계값은 설정으로" 규약은 **리스크 값**에 지켜지고 있다. 거래소 이름과 환경변수 읽기가 흩어진 것이 하드코딩의 실체다. 둘 다 기계적으로 모을 수 있다.

---

## 3. 책임 분리

| 층 | 상태 | 근거 |
|---|---|---|
| 패키지 사이 | **우수** | import-linter 계층 계약 + 금지 계약 3(분석⇏집행 · 탐지기⇏이행률 · LLM⇏결정/집행) · `exhaustive=true` |
| 도메인 안 | **좋음** | 지표·구조물·탐지기·플레이북·평가가 패키지로 갈려 있고 미래 참조 차단이 계약으로 |
| 파일 수준 | **미흡** | `apps/api/walkforward.py` 5,495줄(라우터 + 대조 루프 + 감시자 + 되살리기 + 전역 상태) · `live_runner.py` 5,370줄(주문 · 보호 · 감사 3종 · 입양) · `session.py` 2,943줄 |
| 화면 | **보통** | 순수 계산은 모듈로(`consoleSummary` · `chart/indicators` · `chart/trades`) · `api.ts` 1,569줄 · `ConsoleTab.tsx` 1,237줄 |
| 저장소 트리 | **정리됨(09-06)** | 루트 스크립트 62개 → `runtime/data/dev/build/research` · 문서 폴더 역할별 · 결과 원문 `docs/measurements/` · 링크 래칫 도구 |

오늘 재정리로 **"어디에 무엇이 있나"** 는 답이 됐다. 남은 것은 **"한 파일이 왜 바뀌나"** 다 — [code_quality_review.md §3.1](code_quality_review.md) 의 분리 제안(감시·대조를 `orchestration` 으로, `LiveRunner` 를 협력자 넷으로).

---

## 4. 보안

### 4.1 잘 된 것

| 영역 | 무엇 | 확인 |
|---|---|---|
| 실주문 문 | `APP_ENV=live` **and** `LIVE_ORDERS=1` **and** 키. 하나라도 빠지면 테스트넷. 판정은 순수 함수 | `live_gate.py` · 게이트 밖 어댑터 생성은 AST 검사 실패 |
| 돈의 격리 | 데모 API 는 **별도 프로세스·별도 DB**, `APP_ENV=paper` 면 라이브 키의 존재 자체를 거부 | 게스트가 실계좌 값을 볼 구조적 경로 없음 — 배포 뒤 실측(S5) |
| 세션 | 서명 쿠키 `HttpOnly` · `Secure`(TLS 뒤) · `SameSite=Lax` · 평문이면 dev 외 거부 · OAuth `state` 로 로그인 CSRF 차단 · 1시간 재인증 | `auth.py` |
| 권한 | 역할 5단(pending·viewer·trader·admin·guest) · 경로별 `Need` · 거래 POST 30/분 | 미승인은 읽기만 |
| 헤더 | `X-Frame-Options DENY` · `nosniff` · `Referrer-Policy same-origin` · `Permissions-Policy` · HSTS 는 Caddy | nginx.conf |
| 빌드 컨텍스트 | `.dockerignore` **화이트리스트**(`*` 뒤 `!` 로 연다) → `.env*`·`*.pem`·`logs/`·`data/` 가 데몬에 가지 않는다 | 14 GB → 5.1 MB |
| 컨테이너 | `useradd --uid 1000 updown` · 모든 COPY `--chown` · 비루트 실행 | Dockerfile |
| 감사 로그 | `event_logs` · `risk_plan_revisions` 는 DB 권한으로 INSERT 만 | alembic 0003 |
| 코드 위험 패턴 | `shell=True` 0 · `pickle` 0 · `yaml.load` 0(`safe_load` 17) · `eval/exec` 0(`redis.eval` 은 Lua 락 스크립트) · 원시 SQL 24곳은 전부 `sa.text` **바인드 파라미터** | grep |
| 시크릿 취급 | 커밋 금지 · 프로브 출력은 이름·유무 · Gate 서명은 `hmac` 로 계산만, 로그에 안 찍힘 · 실계좌 env 값 변경은 사람이 서버에서 | `signing.py` · 런북 |
| 테스트 문 | `AUTH_TEST_BYPASS=1` — 명시 설정 + 실계좌면 무시 + 127.0.0.1 만 (세 겹) | 오늘 로컬 펀드 정리에 임시로 쓰고 바로 걷음 |
| 의존성 | `uv.lock` 고정 · Python 3.12 고정 | CI `uv sync --locked` |

### 4.2 미비 · 위험

| 항목 | 위험 | 상태 |
|---|---|---|
| 인증 실패 회로차단기 | 키가 틀리거나 화이트리스트가 빠지면 401/403 이 분당 수십 건 — 실측 1시간 2,498건(S2) | ⬜ 밴 회로차단은 있고 인증 실패용은 없음(S4) |
| 시크릿 수명 | 세션 서명키 회전 · 거래소 키 교체·유출 절차 · 세션 강제 만료 없음 | ⬜ S6 |
| 의존성 취약점 점검 | Dependabot/`pip-audit`/`npm audit` 자동화 없음 | ⬜ 권장: CI 에 `uv run pip-audit` + `npm audit --audit-level=high` |
| 저장소 루트의 개인키 사본 | `tokyo_lightsail_ssh.pem` 이 루트에 있었다(gitignore 는 됐고 `~/.ssh` 와 동일본) | ✅ 오늘 삭제 · `*.pem` 은 dockerignore 화이트리스트 밖 |
| 한 계정 여러 관리자 | 로컬 스택이 서버와 같은 테스트넷 계정을 만졌다(09-06) | ✅ 계정 분리(로컬 Binance) · 연결 안 된 거래소로 판 생성 400 · ⬜ 계정 UUID 기준 코드 가드 |
| 데드맨 | 앱이 7일 넘게 죽으면 브로커 손절 만료 | 보류(사용자 결정) — 운영이 본다 |
| 관리자 부트스트랩 | `ADMIN_EMAILS` 는 **첫 로그인 때만** admin 을 만든다 | 설계 의도 · 문서화됨 |
| 로컬 개발 키 | `.env.dev` 에 Binance 테스트넷 키(페이크머니) — 실계좌 키는 로컬에 없음 | ✓ |
| 요청 크기·본문 제한 | nginx 기본값 의존 | 낮은 위험 · 명시 권장(`client_max_body_size`) |

### 4.3 권장 순서

1. CI 에 의존성 취약점 점검 한 줄씩(`pip-audit` · `npm audit`) — 비용 거의 0.
2. 인증 실패 회로차단 — 밴 회로차단(`ratelimit.meter`)과 같은 모양으로 401/403 을 세어 그 거래소 호출을 잠시 멈춘다.
3. 키 회전 절차 문서 + 세션 강제 만료 스위치(`app_settings`) — S6.
4. `"GATE"` 기본 인자 제거 · `os.environ` 을 `Settings` 로 — 보안이라기보다 "잘못 붙은 곳에 조용히 붙는" 사고 예방.
