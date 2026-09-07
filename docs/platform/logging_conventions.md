# 로깅 규약 — ID 체인 · event_type · 행동 분류

| 항목 | 값 |
|------|-----|
| 근거 | [auto_invest_spec.md](../planning/auto_invest_spec.md) §4.14(ID 체계·손실 귀속) · **§1.2.1(로그 실패 모드)** · P5(로그 우선) · §2.1(structlog) · §8(append-only) · §12.3(UTC) |
| 결정 기록 | [Phase00_Foundation_plan.md](../planning/history/Phase00_Foundation_plan.md) **D-13** |
| 구현 | [`common/logging/`](../../src/updown/common/logging/) · [`apps/api/middleware.py`](../../src/updown/apps/api/middleware.py) · [`apps/engine/log_recovery.py`](../../src/updown/apps/engine/log_recovery.py) |
| 검증 | [`tests/test_logging.py`](../../tests/test_logging.py) · [`tests/test_log_failure_policy.py`](../../tests/test_log_failure_policy.py) |
| 작성 | 2026-08-03 (P0-6) |

---

## 1. 로그는 두 갈래다

같은 "로그"라는 말이 두 가지를 가리키므로 먼저 나눈다. **섞으면 안 된다** — 진단 로그는
유실돼도 돈을 잃지 않지만, 감사 로그가 유실되면 손실 귀속(§4.14)이 불가능해진다.

| | 진단 로그 (stdout) | 감사 로그 (`event_logs`) |
|---|---|---|
| 목적 | 사람이 읽는 운영 관측 | **상태 변화의 증거** |
| 입구 | `get_logger(module).info(...)` | `await audit.record(...)` |
| 구현 | [`logging/setup.py`](../../src/updown/common/logging/setup.py) | [`logging/audit.py`](../../src/updown/common/logging/audit.py) |
| 대상 | stderr/stdout JSON 한 줄 | PostgreSQL `event_logs` 행 |
| 실패 시 | 그냥 사라진다 | **§1.2.1 정책이 발동한다** (→ §5) |
| trace_id 없을 때 | `null` 로 남긴다 | **발급하고 표시한다** |
| 수정·삭제 | — | **불가** (DB 권한으로 회수, 절대 규칙 #8-2) |

> **모든 상태 변화는 감사 로그다.** 주문·체결·스탑 이동·청산·브레이커 발동은
> `audit.record()` 를 거친다. 진단 로그만 남기고 넘어가면 §4.14 리포트에서 그 사건이
> 존재하지 않는 것이 된다.

### 1.1 진단 로그의 고정 필드

필드 이름이 로그마다 다르면 검색·집계가 불가능하다. 최상위는 **6개로 고정**하고,
그 외 키는 전부 `payload` 아래로 모인다 (`_normalize_shape`).

```json
{"ts":"2026-08-03T09:12:44.031Z","level":"info","module":"marketdata.ingest",
 "event_type":"CANDLE_BACKFILL_DONE","trace_id":"9f2c...","payload":{"bars":420}}
```

| 필드 | 규칙 |
|------|------|
| `ts` | ISO8601 **UTC**. 로컬 시각 금지 (§12.3, 절대 규칙 #7) |
| `level` | `debug / info / warning / error / critical` |
| `module` | 도메인 경계가 보이게 `execution.order_service` 처럼 |
| `event_type` | §4 규약. structlog 의 `event` 는 여기로 옮겨진다 |
| `trace_id` | 없으면 `null` — 요청·잡 밖의 로그임을 뜻한다 |
| `payload` | 나머지 전부. 항상 dict (없으면 `{}`) |

표준 `logging` 도 같은 렌더러로 브릿지한다 — uvicorn·SQLAlchemy 로그가 평문으로 섞이면
수집기가 그 라인만 조용히 버린다.

---

## 2. ID 체인

```
trace_id  →  proposal_id  →  order_id  →  position_id     (spec §4.14)
요청·잡 1회    제안 1건       주문 1건      포지션 1건
```

### 2.1 어디에 저장되는가

`event_logs`(§9)에는 **`trace_id` 컬럼만** 있다. 나머지 셋은 `payload_json` 안의
**예약 키**로 들어간다 (`audit.CHAIN_KEYS`). 키 이름을 고정해야 나중에
`payload_json->>'order_id'` 로 조인할 수 있다.

```python
await audit.record(
    event_type="ORDER_FILLED",
    module="execution.order_service",
    risk=ActionRisk.RISK_INCREASING,
    proposal_id=proposal.id,          # ← 예약 키. 아는 것은 다 넘긴다
    order_id=order.broker_order_id,
    payload={"filled_qty": str(qty)},
)
```

### 2.2 trace_id 만 ContextVar 인 이유

`trace_id` 는 실행 흐름 1회의 범위라 모든 함수에 인자로 끌고 다니는 것이 비현실적이다.
반면 나머지 셋은 **특정 제안·주문에 매인 값**이다 — 컨텍스트에 두면 한 요청이 제안 2건을
다루는 순간 어느 것의 ID인지 헷갈리는 지점이 생긴다. 그래서 이벤트별 명시가 정확하다.

### 2.3 trace_id 발급 규칙

| 경로 | 규칙 | 구현 |
|------|------|------|
| HTTP 요청 | `X-Trace-Id` 있으면 **승계**, 없으면 발급. 응답에도 실어 보낸다 | `TraceIdMiddleware` |
| engine 스케줄 잡 | **잡 실행 1회마다** `trace_context()` 로 감싼다 (§4.14) | 등록은 P0-9-7 |
| 그 외(부팅 등) | 진단 로그는 `null`. 감사 로그는 발급 + 표시 | `AuditLogger.record` |

승계 값은 `^[0-9a-f]{8,64}$`(대소문자 무관)만 허용한다. 검증 없이 승계하면 개행·제어문자가
JSON 로그 필드로 그대로 찍혀 수집기가 오독한다 — **로그 인젝션**이다 (§8). 형식이 어긋나면
400 이 아니라 **조용히 새 값을 발급**한다: trace_id 는 관측 수단이지 기능 파라미터가 아니고,
잘못된 헤더 때문에 정상 요청을 막을 이유가 없다.

**컨텍스트 밖의 감사 로그**는 `payload.trace_synthesized = true` 가 붙는다.
`event_logs.trace_id` 가 NOT NULL 이라 발급이 강제되지만, 그 값은 **다른 어떤 로그와도
이어지지 않는다.** 표시가 없으면 나중에 "왜 이 이벤트만 고립돼 있지?"를 조사하게 된다.

---

## 3. 호출부 사용 패턴

**로그를 먼저 남기고 행동한다** (P5). 그리고 **`may_proceed` 를 반드시 확인한다.**

```python
attempt = await audit.record(
    event_type="ORDER_SUBMITTED",
    module="execution.order_service",
    risk=ActionRisk.RISK_INCREASING,      # ← 생략하면 보류로 떨어진다
    proposal_id=proposal.id,
)
if not attempt.may_proceed:
    return                                 # 감사 추적 없이 리스크를 늘리지 않는다
await gateway_adapter.place_order(request)
```

리스크 감소 행동은 형태가 반대다 — 판정은 항상 `True` 지만, 그래도 호출한다.
그래야 폴백 기록과 실패 알림이 발생한다.

```python
attempt = await audit.record(
    event_type="STOP_LOSS_EXECUTED",
    module="execution.order_service",
    risk=ActionRisk.RISK_REDUCING,
    position_id=position.id,
)
await gateway_adapter.place_order(stop_order)   # attempt 와 무관하게 집행한다
```

`record()` 는 **예외를 던지지 않는다.** 적재 실패로 예외가 올라가면 호출부의 정상 경로가
끊기고, 그것이 곧 "로그 실패가 손절을 막는" 상황이다 (§1.2.1). 실패는 반환값으로만 표현한다.

---

## 4. `event_type` 네이밍

**`<대상>_<과거형 동작>`, 대문자 SNAKE_CASE.** 일어난 사실을 적는다 — 의도(`SUBMIT_ORDER`)가
아니라 결과(`ORDER_SUBMITTED`)다. 감사 로그는 "무엇을 하려 했는가"가 아니라 "무엇이
일어났는가"의 기록이다.

- 대상을 앞에 둔다 → `ORDER_*`, `STOP_*`, `POSITION_*` 로 prefix 검색이 된다
- 종목·수량·가격 같은 **가변 값은 이름에 넣지 않는다.** `payload` 로 간다
- 실패도 이벤트다 → `ORDER_REJECTED`, `BREAKER_TRIPPED`

### 4.1 초안 (P0 범위)

| `event_type` | 시점 | 기본 분류 |
|---|---|---|
| `BOOT_COMPLETED` | 프로세스 기동 완료 | READ_ONLY |
| `CONFIG_LOADED` | 설정 로드 (값은 남기지 않는다) | READ_ONLY |
| `CANDLE_BACKFILL_DONE` | 백필 배치 완료 (P0-8) | READ_ONLY |
| `CANDLE_QUALITY_ISSUE_OPENED` | 무결성 위반 기록 (§12.1) | READ_ONLY |
| `JOB_STARTED` / `JOB_FINISHED` / `JOB_FAILED` | 스케줄 잡 (P0-9) | READ_ONLY |
| `LOG_RECOVERY_DONE` | 폴백 이관 완료 | READ_ONLY |

### 4.2 예약 (P1~P3 에서 구현과 함께 확정)

| `event_type` | 시점 | 기본 분류 |
|---|---|---|
| `SETUP_DETECTED` | 셋업 탐지 (P1) | READ_ONLY |
| `PROPOSAL_CREATED` / `PROPOSAL_APPROVED` / `PROPOSAL_REJECTED` | 제안 수명주기 | READ_ONLY |
| `RISK_PLAN_REVISED` | 손절·익절 계획 변경 (`risk_plan_revisions` 와 짝) | 상황별 → §6 |
| `ORDER_SUBMITTED` / `ORDER_FILLED` / `ORDER_REJECTED` | 진입 레그 | **RISK_INCREASING** |
| `ORDER_CANCELLED` | 주문 취소 | **RISK_REDUCING** |
| `STOP_RAISED` | 스탑 상향 (하향은 존재할 수 없다 — 절대 규칙 #3) | **RISK_REDUCING** |
| `TAKE_PROFIT_SUBMITTED` | 익절 주문 (1차 체결 후 재평가 → 2차) | **RISK_REDUCING** |
| `POSITION_CLOSED` | 청산 | **RISK_REDUCING** |
| `STOP_LOSS_EXECUTED` | 손절 집행 | **RISK_REDUCING** |
| `BREAKER_TRIPPED` | 서킷브레이커 발동 (§4.16) | **RISK_REDUCING** |
| `REGIME_CHANGED` | 레짐 전환 (§4.15) | READ_ONLY |

새 `event_type` 을 추가할 때는 **이 표에 분류를 함께 적는다.** 분류가 비어 있으면
기본값(보류)이 적용되어, 리스크 감소 행동이 로그 장애 때 막힐 수 있다.

---

## 5. 행동 분류 — 로그 실패 시 무엇을 하는가 ⭐

D-13 / §1.2.1. P5("로그를 먼저 남긴다")를 문자 그대로 구현하면 **로그 DB 장애가 손절을
막는다.** 감사 추적을 지키려다 원금을 잃는 역전이다. 그래서 방향을 나눈다.

| 분류 | 뜻 | 로그 실패 시 | 폴백 파일 |
|------|-----|-------------|----------|
| `RISK_REDUCING` | 노출을 **줄이는** 행동 | **무조건 집행** | 기록한다 |
| `RISK_INCREASING` | 노출을 **늘리는** 행동 | **보류** | 불필요(행동이 없었다) |
| `READ_ONLY` | 상태를 바꾸지 않음 | 보류(무해) | 불필요 |
| *(미선언)* | — | **보류** | 불필요 |

### 5.1 판정 기준

> **"이 행동을 건너뛰면 손실 가능성이 커지는가?"** → 그렇다면 `RISK_REDUCING`.

| RISK_REDUCING | RISK_INCREASING |
|---|---|
| 손절 집행 | 신규 진입 |
| 포지션 청산(전량·부분) | 추가 매수 / 다음 진입 레그 |
| 스탑 **상향** | 버킷 비중 확대 |
| 미체결 주문 취소 | 레버리지·파생 노출 증가 |
| 익절 주문 제출 | |
| 서킷브레이커 발동 | |

애매한 것은 `RISK_INCREASING` 이다. 판단이 갈리는 행동을 감소 쪽에 넣으면 잘못된 무단
집행이 되고, 증가 쪽에 넣으면 최악이 "장애 중 한 번 못 했다"에 그친다.

### 5.2 기본값은 보류다

`must_proceed_despite_log_failure(None) is False`. **`DEFAULT_ACTION_RISK` 의 방향이 정책
전체의 안전성을 결정한다** — `RISK_REDUCING` 이 기본값이면 앞으로 추가될 모든 행동이
분류를 잊은 채 무단 집행된다. 이 상수는 테스트로 고정돼 있다
(`test_unclassified_action_defaults_to_hold`).

### 5.3 실패 처리 순서

```
1) event_logs INSERT
   └─ 성공 → 끝. 행동 진행 가능
2) 실패
   ├─ RISK_REDUCING → 폴백 JSONL append → **집행 허용**
   │                  └─ 폴백까지 실패 → 그래도 **집행 허용** (사유를 알림에 담는다)
   ├─ 그 외          → **보류**
   └─ 어느 경우든 ── 실패 자체를 알린다 + 헬스 상태를 degraded 로
```

**로그 실패 알림은 분류와 무관하다.** 조용한 로그 유실은 §7 "조용한 실패 금지" 위반이고,
보류된 경우에도 운영자는 "왜 진입이 안 됐는지"를 알아야 한다.

통지는 **structlog 을 타지 않는다** (`StderrLogFailureNotifier` → `sys.stderr` 직접).
고장난 로그 경로로 그 고장을 알리면 통지도 함께 죽는다. 실제 알림 채널은 P2-5 에서 연결한다.

그리고 `/health` 가 `LogHealthState` 를 노출한다 (P0-9-1). 데드맨 스위치(§12.6)와 같은
발상이다 — **로그가 죽은 것을 로그로만 알리면 아무도 모른다.**

---

## 6. 감사 추적 = DB + 폴백 파일의 합집합

이관 배치가 돌기 전까지는 `SELECT * FROM event_logs` 가 **전체가 아니다.** DB 장애 중
집행된 리스크 감소 행동은 `logs/event_logs_fallback.jsonl` 에만 있다.

```
감사 추적 = event_logs ∪ logs/event_logs_fallback.jsonl
```

조사·손실 귀속(§4.14) 시 **두 곳을 다 본다.** 폴백 파일이 비어 있음을 먼저 확인하는 것이
순서상 맞다 (`FallbackSink.is_empty()`, 또는 `/health` 의 degraded 플래그).

| 항목 | 값 |
|------|-----|
| 형식 | JSON Lines — append 만으로 유효한 파일이 유지된다. JSON 배열이면 프로세스가 중간에 죽는 순간 파일 전체가 파싱 불가가 된다 |
| 경로 | `logs/event_logs_fallback.jsonl` (`.gitignore` 대상 — 종목·수량·계좌가 들어간다, §8) |
| flush | **매 레코드마다.** 버퍼에 남은 채 죽으면 폴백의 존재 이유가 없다 |
| 이관 | `recover_fallback_logs()` — 읽기 → 한 트랜잭션 INSERT → **커밋 뒤에만** 파일 비우기 |
| 재실행 | 안전하다. `event_id` 를 앱에서 생성하므로 `ON CONFLICT (id) DO NOTHING` 이 중복을 막는다 |
| 깨진 줄 | **예외.** 조용히 건너뛰면 일부를 잃은 채 파일을 비워 감사 추적이 사라진다 (§7) |
| 롤 | `updown_logrecovery` — 앱 롤과 분리해야 "이관만 하는 경로"를 권한으로 증명할 수 있다 |

**비우기 → 이관 순서는 절대 하지 않는다.** 이관 중 실패하면 레코드가 영구 유실되고,
그것은 append-only 감사 로그를 두는 이유 전체를 무너뜨린다.

---

## 7. append-only 두 테이블은 짝이다

| 테이블 | 무엇의 증거인가 | 권한 |
|--------|----------------|------|
| `event_logs` | **무엇이 일어났는가** — 상태 변화 전체 | `SELECT, INSERT` |
| `risk_plan_revisions` | **손절선이 어떻게 움직였는가** — 하향 금지의 증거 | `SELECT, INSERT` |

둘 다 `REVOKE UPDATE, DELETE, TRUNCATE` 다 (0003 마이그레이션, 절대 규칙 #8-2).
근거는 같다: **조작 가능한 증거는 증거가 아니다.** `risk_plan_revisions` 는 직전 값이
남아 있어야 "스탑이 내려간 적 없다"(§6.9, 절대 규칙 #3)를 검증할 수 있고, `event_logs` 는
사후 수정이 가능하면 손실 귀속 리포트를 신뢰할 수 없다.

`ON CONFLICT DO NOTHING` 은 UPDATE 가 아니라 **삽입 생략**이므로 이 권한과 충돌하지 않는다.

D-13 정책이 이 짝을 완성한다 — 권한으로 **수정**을 막고, 폴백으로 **유실**을 막는다.
둘 중 하나만 있으면 감사 추적에 구멍이 남는다.

---

## 8. 금지 사항

| # | 금지 | 이유 |
|---|------|------|
| 1 | `event_logs` 에 UPDATE/DELETE | 권한으로 차단됨 — 시도하면 런타임 에러 (절대 규칙 #8-2) |
| 2 | `print()` 로 운영 로그 | 수집기가 파싱 못 한다. 예외는 `StderrLogFailureNotifier` 하나 |
| 3 | `attempt.may_proceed` 무시 | 감사 추적 없이 리스크를 늘리게 된다 |
| 4 | 주 로직 트랜잭션에 감사 로그 태우기 | 로그 실패가 주 로직을 롤백시킨다 — §1.2.1 이 막으려는 역전 |
| 5 | payload 에 시크릿·자격증명 | §8. `default=repr` 이 `SecretStr` 을 `**********` 로 막지만 방어선일 뿐이다 |
| 6 | 로컬 시각 타임스탬프 | 컨테이너 타임존에 따라 로그 순서가 뒤바뀐다 (§12.3) |
| 7 | 분류 없이 새 행동 추가 | 기본값 보류에 걸려 리스크 감소 행동이 장애 중 막힌다 |

---

## 9. 미해결 / 다음 Phase

| 항목 | Phase |
|------|-------|
| `/health` 에 `LogHealthState` 노출 + DoD 1 문자 그대로 검증 | P0-9-1, P0-9-6 |
| 스케줄러에 `recover_fallback_logs` 등록 + 잡 단위 `trace_context()` | P0-9-7 |
| 실패 알림을 실제 채널(이메일·푸시)로 교체 | P2-5 |
| 폴백 파일 대용량 시 배치 분할 (부분 성공 후 지운 레코드 추적) | P2 운영 경험 후 |
| `event_type` 표 확장 — 신규 항목은 **분류를 함께** 적는다 | P1~P3 |
