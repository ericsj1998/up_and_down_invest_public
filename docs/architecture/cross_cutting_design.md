# 횡단 관심사 설계 — 바깥 호출 · 캐시 · 소프트 삭제 · 루프 눈금

> 도메인이 아닌데 모든 도메인이 쓰는 것들을 **어디에 두고 어떻게 한 벌로 만들었나**. 2026-09-10 사용자 요청("API 호출을 한곳에서 관리하는
> 레이어 · 표준적으로 분리하는 것들 · 비동기 · 소프트 딜리트")에서 나온 설계다. 원문 태스크: [T264](../planning/tasks/T264_outbound_http_layer.md) ·
> [T265](../planning/tasks/T265_async_audit.md) · [T266](../planning/tasks/T266_soft_delete_review.md).

## 0. 어디에 사나 — `common` 의 배관

```
src/updown/common/
├── http/        아웃바운드 HTTP 한 층 — Outbound · RetryPolicy · Throttle
├── cache.py     TTL 캐시 한 벌 — TtlCache · 등록부
├── resources.py 자원 스냅샷 — 프로세스 · cgroup · 디스크 · loop_lag_ms · caches
├── logging/     구조화 로그 · 파일 싱크 · event_logs 싱크
├── config.py    설정 한 번 읽기 · 누락 즉시 중단
├── db/          async 엔진 · 세션 팩토리 · 모델
├── lock/        Redis 락 (거래 리더)
└── security/    권한 판정 (순수 함수)
```

`common` 은 "도메인 I/O 를 하지 않는다"(시세·주문을 모른다)는 뜻이지 배관까지 금지가 아니다 — `db` · `logging` 이 그렇듯 **무엇을 나르는지
모르는 배관**은 계층 바닥에 있어야 `marketdata` · `llm` · `apps` 가 다 쓴다.

### 표준적으로 분리하는 것들 — 점검표 (2026-09-10)

| 관심사 | 어디 | 상태 |
|---|---|---|
| 로깅 | `common/logging` — structlog JSON · 파일 싱크 · `event_logs`(추가 전용) | ✅ |
| 설정·시크릿 | `common/config.Settings` | ✅ |
| DB 세션 | `common/db/session` (async) | ✅ |
| 인증·권한 | `common/security` + `apps/api/auth` | ✅ |
| 분산 락 | `common/lock/redis_lock` | ✅ |
| 비용 표 · 능력표 · 달력 | `common/costs` · `domain/capabilities` · `domain/session` | ✅ |
| **아웃바운드 HTTP** | `common/http` (§1) | ✅ 2026-09-10 |
| 스로틀 · 요율 눈금 | `common/http/throttle` · `marketdata/ratelimit`(거래소 헤더 해석은 거래소를 아는 쪽에) | ✅ |
| **TTL 캐시** | `common/cache` (§2) | ✅ 2026-09-10 |
| 백그라운드 작업 | `apps/api/jobs.JobRegistry` | ✅ |
| 이벤트 로그 | `event_logs` — DB 권한으로 UPDATE/DELETE 차단 | ✅ |
| 시계 주입 | 결정론 코어는 봉 시각을 쓴다. 러너·API 의 "지금" 은 직접 읽는다 | 🟡 필요해지면 `Clock` |
| 알림 | SMTP 하나 — 채널이 둘이 되면 `common/notify` | ⚪ |

## 1. 아웃바운드 HTTP 한 층 (`common/http`)

### 왜

실측(2026-09-10) `httpx.AsyncClient(` 가 소스에 **11곳**, 그중 다섯(토스 · EDGAR · 업비트 · Gate · 바이낸스)이 재시도 루프를 **각자** 들고 있었다.
`Retry-After` 를 읽는 곳 셋, 안 읽는 곳 둘, 지터 있음/없음, 로그 이름 다섯 가지, 요청을 세는 곳은 토스뿐. `Retry-After` 상한이 토스 → EDGAR 로
복사돼 두 벌이 됐다. 고칠 때 다섯 군데를 고쳐야 했다.

### 무엇을 아는 층인가

| 층이 안다 | 클라이언트에 남는다 |
|---|---|
| 재시도 대상 상태(429 · 5xx · 전송 오류) · 지수 백오프 + 지터 · `Retry-After`(정수 초 · 상한 120s) | 응답 봉투(`result` 안쪽 · `choices[0]`) |
| 요청 세기 + 예산 `budget(cap)` (판 시작 요청 상한 T253) | 인증 — 토큰 발급 · HMAC 서명은 헤더/본문으로 넣어 준다 |
| 호출당 로그(출처 · 경로 · 상태 · 지연 · 시도) — 쿼리는 뗀다(키·서명이 섞인다) | 상태 코드 → **도메인 예외**(404 = 모르는 CIK · 401 = 자격증명) |
| 스로틀 훅(`throttle_of(키)`) · 응답 헤더 훅(`on_response` → 요율 눈금) | 무엇을 한 그룹으로 볼지(업비트·토스 = 그룹 · Gate = 엔드포인트) |
| 닫힌 풀 재개방(공유 클라이언트를 누가 닫아도 다음 요청이 다시 연다) | — |

```python
Outbound("EDGAR", timeout=30, headers={...}, policy=RetryPolicy(max_retries=3, retriable={429, 403}),
         throttle_of=lambda _p: throttle, transport=transport)
response = await http.request("GET", url)          # 재시도 대상이 아닌 응답은 그대로 돌려준다
body = await http.get_json(url)                    # 200 아님·JSON 아님 → OutboundError(status_code 들고)
```

### 정책은 값만 다르고 모양은 같다

| 출처 | 정책 |
|---|---|
| EDGAR | 3회 · 429·403·5xx · `Retry-After` · IP 전체 스로틀 8/s |
| 토스 | 3회 · 그룹 스로틀 · 토큰 발급도 층(`throttle_key="AUTH"`) · 401 한 번 재발급은 클라이언트 |
| 업비트 · Gate 공개 · 바이낸스 공개 | 3~4회 · 429·5xx |
| 거시(야후·연준·BLS·CBOE) | 1회 · 화면이 기다리는 경로 |
| NVIDIA · 구글 토큰 교환 | **`NO_RETRY`** — 실패를 값으로 다루거나 코드가 1회용 |
| **Gate · 바이낸스 주문** | **`NO_RETRY`** — 어떤 상태도 재시도 대상이 아니다(응답 그대로). 주문이 생겼을 수 있는 5xx 를 층이 삼키면 안 된다. 재시도는 부르는 쪽이 **체결 조회 뒤에**(절대 규칙 #6). Gate 본문은 서명한 문자열을 `content=` 로 바이트 그대로 |

### 기계가 지킨다

- `tests/test_outbound_layer.py::TestRawClientRatchet` — 원시 `httpx.AsyncClient(` 를 만드는 파일 목록을 못 박는다. 지금 **2곳**(층 자체 · 웹소켓 핸드셰이크).
  새 파일이 만들면 실패, 옮긴 파일이 목록에 남아도 실패 — 목록은 줄기만 한다.
- `tests/test_trade_clients_outbound.py` — 주문 클라이언트는 전송 계층을 가로채 **나간 요청으로 서명을 다시 계산**해 일치를 본다(본문 바이트 · 쿼리 순서 ·
  502/503 은 한 번만 · 바이낸스 -1021 만 두 번). 로컬 바이낸스 테스트넷 서명 조회로 실통과.

## 2. TTL 캐시 한 벌 (`common/cache.TtlCache`)

같은 `dict[키, (시각, 값)]` 을 여덟 곳이 손으로 짜고 있었다. 세 결함이 반복됐다 — 같은 키를 동시에 물으면 **둘 다 나가고**(판 6개가 같은 잔고를 여섯 번),
만료 항목을 안 걷어 **영원히 자라고**, 어디가 얼마나 기억하는지 **한눈에 볼 수 없었다**.

| 규칙 | 왜 |
|---|---|
| **실패는 기억하지 않는다** | 실패를 캐시하면 요율 제한 한 번이 TTL 만큼 이어진다 |
| **같은 키는 한 번만 나간다** (키 락 단일 비행) | `shared_read` 가 생긴 이유(5분에 `account` 266회)를 락이 직접 막는다. 다른 키는 막지 않는다 |
| **`None` 도 값이다** — `fresh()` 가 `(시각, 값)` 을 준다 | 토스 장중 상태 "모른다 = None" 을 기억해야 한다 |
| **낡은 값은 `peek()`** | 청산 이력·미실현은 실패 시 "마지막 값" 을 내는 것이 빈 화면보다 낫다 |
| 이름을 붙여 등록하면 자원 스냅샷 `caches` 에 크기·적중 | "왜 60초 전 값이 보이지" 를 코드로 찾지 않는다 |

| 자리 | 이름 | TTL |
|---|---|---|
| 콘솔 거래소 상태 · 청산 이력 | `exchange.state` · `exchange.closes` | 3/20/30s · 120s |
| 거시 지표 · 재무 순위 | `macro` · `fundamentals.ranking` | 60s · 600s |
| 펀드 미실현 · 상세 | `fund.unreal` · `fund.members` | 20s · 300s |
| 계약 명세 · 계정 공유 조회 | `marketdata.spec` · `marketdata.shared_read` | 3600s · 2s |
| 토스 장중 상태 · 페이퍼 호가 (인스턴스) | — | 60s · 1s |

재빌드 뒤 실측: `shared_read` 적중 20/33 — 판들이 겹쳐 묻던 것이 합류한다.

## 3. 소프트 삭제 — 무엇을 지우고 무엇을 남기나

### 원칙

1. **다른 행이 그것을 가리키면 소프트다** — 판이 펀드를, 리포트가 대화를, 권한·토큰이 계정을 가리킨다.
2. **"현재 상태" 표는 하드다** — 권한 · 등급 묶음 · 설정. 이력은 `event_logs`(불변)가 맡는다.
3. **재생성 가능한 파일은 하드다** — 라벨 · 로그 · 진행 파일.
4. 열 이름은 관례대로 **`*_at`**(`closed_at` · `revoked_at` · `deleted_at` · `dropped_at`). `is_deleted` 불리언은 두지 않는다 — 언제가 없다.

### 지금

| 대상 | 방식 | 왜 |
|---|---|---|
| 판 · 체결 (`wf_runs` · `wf_trades`) | `closed_at` (부분 유니크 `closed_at IS NULL`) | 돈의 기록 — 처음부터 소프트 |
| API 토큰 | `revoked_at` | 되돌린 토큰이 언제까지 살았는지 |
| **계정** | `deleted_at` + `blocked` · 같은 트랜잭션에서 **토큰 되돌림 · 권한 행 삭제** · 재로그인은 대기 계정으로 되살림 | 하드 삭제 때 이메일로 매인 권한·토큰이 남아 **재가입 때 옛 권한이 그대로 붙었다**(보안 구멍 · 2026-09-10 발견) |
| **펀드** | 정의 파일을 `logs/funds/archive/` 로 보관 + `dropped_at` | 판·체결은 남는데 이름·바스켓이 사라져 리포트가 "펀드 ?" 가 됐다 |
| **대화** | `deleted_at` — 목록·열기만 숨김 | 도구 호출·모델 원가가 대화에 묶여 있어 지우면 리포트 합계가 바뀐다 |
| 한도 해제(`app_settings` 빈 값) | 하드 + `event_logs.setting_cleared`(누가 · 전 값) | 리스크 **증가** 행동은 누가 했는지 남아야 한다 |
| 권한 · 등급 묶음 | 하드 (지우기 전 값을 `event_logs`) | 현재 상태 표 |
| `event_logs` | UPDATE/DELETE 자체가 DB 권한으로 막힘 | 감사 |

## 4. 비동기 — 무엇이 루프를 막나 (눈금부터)

정적 점검 결론: **I/O 는 전부 비동기다**(DB psycopg async · Redis · httpx · 웹소켓 · 동기 SQLAlchemy 0 · `time.sleep` 0). 부족한 것은 "비동기가
아닌 I/O" 가 아니라 **이벤트 루프 위에서 도는 CPU 작업** — 특히 러너가 부르는 `Session.step()`(지표·구조물 계산 · 판이 N 개면 N 번). "느릴 것 같다"
로 `to_thread` 를 깔면 판 락이 생기고 그 락이 새 버그 자리가 되므로 **눈금이 먼저**다.

| 눈금 | 어디 | 값 |
|---|---|---|
| `step_ms` | 러너가 걸음마다 재서 최근 200걸음 · `GET /walkforward/state.step_ms{n,last,p50,max}` · 250ms 넘으면 `live_step_slow` | 평균은 안 낸다 — p50 이 평소, max 가 최악 |
| `loop_lag_ms` | 1초 타이머가 실제로 몇 ms 늦게 깨는지 · api·engine 프로세스 · 자원 스냅샷 · 500ms 이상 경고 | 재빌드 직후 api 1.6ms · engine(in api) 261.6ms |

하루 실측 뒤 셋 중 하나를 정한다: 그대로 / `to_thread` + 판 락 / 판을 엔진 프로세스로. 결정론 코어(`analysis` · `decision` · 세션)는 **동기 순수 함수로
두고 밖에서 내린다** — 코어에 `async` 가 들어가면 시험·재현이 무거워진다. 워커를 늘리는 것은 답이 아니다(판 상태가 메모리에 있어 워커가 둘이면 판이 갈린다).

## 5. 변경 이력

- 2026-09-10 — 첫 판. T264(층 1~3차 · 캐시 통합) · T265(눈금) · T266(소프트 삭제 1~4) 반영.
