# 업 앤 다운 (up_and_down_invest) — 개발 규약 (공개본)

코인 선물 매매법을 백테스트 → 모의 라이브 → 실계좌로 같은 코드로 굴리는 플랫폼. 매매법은 플러그인으로 붙는다
(`docs/platform/strategy_authoring.md`). 아래는 이 저장소의 코드가 따르는 규약이다.

---

## 코딩 규약

### 1. 추상화와 책임 분리 (최우선)

스펙 §3.1 레이어와 원칙 P4(**분석 ≠ 결정 ≠ 집행**)를 코드 구조로 강제한다.

```
src/updown/
├── common/          도메인 모델·설정·로깅·비용 테이블 (아무것도 의존하지 않음)
├── marketdata/      시세/주문 I/O — BrokerAdapter 뒤로 브로커 차이를 숨긴다
├── portfolio/       Unified Portfolio — 사실 집계, 읽기 전용
├── analysis/        지표·구조물·셋업 탐지·Trend — "제안"만 한다
├── decision/        RiskManager·Regime·브레이커·Allocation — 손절/익절/수량을 "확정"한다
├── execution/       주문 감시·집행 — 값을 바꿀 권한이 없다
├── orchestration/   백테스트·추천·Instant Analysis·Charting 투영 — 조립만 한다
└── apps/{api,engine}
```

**의존 방향** (import-linter `layers` 계약으로 CI 강제):

```
common → marketdata → portfolio → analysis → decision → execution → orchestration → apps
(왼쪽이 하위. 상위는 하위를 import 가능, 역방향 금지)
```

- **`orchestration/` 입주 조건**: 자체 판단 로직이 없고 여러 도메인을 **호출만** 하는 모듈. 계산·판단이 생기면 해당 도메인으로 내려보낸다.
- 백테스트 엔진을 `analysis/`에 두면 "분석 → 집행 import 금지"를 위반한다. 같은 이유로 추천·Instant Analysis도 `orchestration/`이다.
- 비용 테이블(§12.7)은 백테스트·페이퍼·RiskManager가 공유하므로 `common/costs.py`에 둔다.

- **한 모듈은 한 가지 이유로만 바뀌어야 한다.** 분석 로직 변경이 리스크 정책이나 주문 집행 코드를 건드리면 경계가 잘못된 것이다.
- **외부 세계는 항상 인터페이스 뒤에 둔다** — 브로커 API는 `BrokerAdapter`, 셋업 룰은 `SetupDetector` 프로토콜.
  상위 도메인은 토스/업비트/백테스트/페이퍼의 차이를 몰라야 한다.
- **임계값·배수는 코드에 박지 않는다** — 설정(YAML/DB)에서 주입한다 (§4.3.1).
- **새 차트 개념 추가 = 탐지 파일 1개(`register()`) + entry point 1줄(`pyproject.toml`) + 룰 설정 1개.**
  기존 코드를 고쳐야 한다면 설계가 틀린 것이다. 플랫폼은 탐지 모듈의 이름을 모른다 (T224 · `analysis/plugins.py`).
- 도메인 경계는 `import-linter` 로 CI에서 강제한다 (P0-2).

### 2. 주석 · Docstring — Google 스타일

모든 공개 함수·클래스·모듈에 Google 스타일 docstring을 단다. 본문은 한국어.

```python
def calculate_position_size(
    account_equity: Decimal,
    risk_pct: Decimal,
    planned_avg_entry: Decimal,
    stop_loss: Decimal,
) -> Decimal:
    """계좌 리스크 비율 기준으로 총 진입 수량을 산정한다.

    분할 진입이라도 리스크는 항상 **계획 평단** 기준으로 계산한다 (spec §4.6).

    Args:
        account_equity: 계좌 총 평가금액.
        risk_pct: 1회 거래 허용 리스크 비율. 0.01 이면 1%.
        planned_avg_entry: 분할 진입 계획의 가중 평단가.
        stop_loss: RiskManager가 확정한 손절가.

    Returns:
        주문 총 수량. 호가단위 라운딩 전 값이며, 라운딩 후 RR 재검증이 필요하다 (spec §12.2).

    Raises:
        ValueError: `planned_avg_entry <= stop_loss` 인 경우. 롱 온리 전제 위반이다.
    """
```

- 섹션은 `Args / Returns / Raises / Note` 를 쓴다.
- **"무엇을"이 아니라 "왜"를 적는다.** 코드를 읽으면 아는 내용을 반복하지 않는다.
- 스펙 근거가 있는 규칙에는 `(spec §4.6)` 처럼 섹션 번호를 남긴다 — 나중에 규칙을 바꿀 때 근거를 잃지 않는다.
- 타입 힌트가 있으므로 docstring에 타입을 중복 기재하지 않는다.

### 3. 도구 체인

| 항목 | 값 |
|------|-----|
| 언어 | **Python 3.12 고정** / TypeScript(프론트) |
| 패키지 | **uv** (pip 직접 사용 금지) · `src/` 레이아웃 |
| DB | PostgreSQL 16 + SQLAlchemy 2.0 + Alembic · 드라이버 **psycopg3** (`postgresql+psycopg://`) |
| 캐시·락 | **redis-py** (`redis.asyncio`) — 락은 `SET NX PX` + TTL 갱신 직접 구현 |
| 컨테이너 | Docker Compose **base + override** — `compose.base.yml` + `compose.{dev,paper,live}.yml`. 서비스 정의 복제 금지 |
| 린트·포맷 | **ruff** |
| 타입 | **pyright** — `Any` 남용 금지 |
| 테스트 | **pytest** + 결정론 코어 골든 테스트 |
| CI | **GitHub Actions** — ruff → pyright → import-linter → pytest |

### 4. 절대 규칙 (스펙 원칙 — 위반 시 리뷰 반려)

| # | 규칙 | 근거 |
|---|------|------|
| 0 | **브로커 어댑터를 직접 생성/import하지 않는다.** 주문 경로는 반드시 `OrderGateway`를 통해 얻는다 — 게이트가 어댑터 획득을 독점한다 | §12.4 |
| 1 | **시크릿을 커밋하지 않는다.** `.env*` 는 `.env.example` 외 전부 gitignore | §8 |
| 2 | **AI가 가격·수량·타이밍을 결정하지 않는다.** LLM은 근거 요약·설명문 생성 전용<br>⭐ **v2.5 단서**: LLM 이 가격을 산출해 `LlmProposal` 로 **표시·기록·채점**하는 것은 허용된다(AI 차트 분석 = 사람을 돕는 분석). 금지는 그 값이 **주문이 되는 경로**이며 집행값의 SSoT 는 언제나 RiskManager 다 — `llm/` 은 `decision/`·`execution/` 을 import 할 수 없다 | P2, **§5.3.1** |
| 3 | **손절선 하향 조정 금지, 상향만 허용.** 우회 경로(버킷 전환 등)도 차단 | §6.9, §4.8 |
| 4 | **손절/익절의 SSoT는 RiskManager.** 분석은 제안만, 집행은 값을 못 바꾼다 | §5.1 |
| 5 | **동일 입력 → 동일 출력.** 결정론 코어에 난수·현재시각 직접 참조 금지 | P1 |
| 6 | **모든 주문에 멱등키.** 재시도 전 반드시 체결 여부를 먼저 조회한다 | §4.10, §7 |
| 7 | **타임스탬프 저장은 UTC.** 표시만 KST, 개장 시각 하드코딩 금지 | §12.3 |
| 8 | **조용한 실패 금지.** 설정 누락·인증서 만료는 즉시 중단 + 알림 | §7 |
| 8-1 | **로그 적재 실패가 리스크 감소 행동을 막지 않는다.** 손절·청산·스탑 상향·주문 취소는 로그가 실패해도 **무조건 집행**하고 폴백 파일에 남긴다. 신규 진입 등 리스크 증가 행동만 보류한다. **분류를 명시하지 않으면 기본값은 보류** | **§1.2.1** |
| 8-2 | **`event_logs`에 UPDATE/DELETE 하지 않는다.** DB 권한으로 막혀 있으므로 시도하면 런타임 에러가 난다 | §1.2.1, §8 |
| 9 | **지표는 자체 구현.** 라이브러리 위임 금지(pandas-ta는 테스트 대조용) | §2.1 |
| 10 | ~~**롱 온리**~~ → **양방향 허용** (2026-08-17 사용자 확정). 숏은 **모의 라이브(T13)와 Gate.io 선물** 경로에서만 열린다. 현물(업비트)은 여전히 롱 온리이며, 숏 계획을 현물 어댑터로 보내면 즉시 예외다 | §12.8 개정 |
| 11 | **사람 눈으로 정답지를 만들지 않는다.** 차트 보고 정탐/오탐 라벨링·구간 라벨링·육안 채점 전부 금지. 대체: **백테스트 성과 + 후속 이행률**(규칙 측정), 구간은 **객관 수치**(R²·기울기·낙폭). ⭕ **규칙 명세가 타당한지 사람이 검토하는 것은 필수** — 구분 기준은 "그 판단이 규칙으로 환원되어 코드에 남는가" | **§5.6.6** |
| 12 | **권위가 아니라 성과가 판정한다.** 강의도 표준 이론도 정답이 아니라 **후보**다. 정의가 갈리면 근거 있는 **최대 3개**를 붙여 **out-of-sample** 로 판정하고 승자를 고정한다. **한 번에 한 축**, 표본 30건 미달이면 기존 유지. 경쟁 대상은 [docs/rules/rule_candidates.md](docs/rules/rule_candidates.md) 목록에만 있다 | **§5.6.7** |

---

## 작업 방식

- 게이트(G0~G2)를 통과하지 못하면 **다음 Phase로 넘어가지 않는다**. 특히 **G1 미달 상태의 실거래는 금지**다.
- 새 파일을 만들기 전에 해당 도메인 패키지의 `README.md`(책임 정의)를 확인한다.
- 모든 상태 변화는 **로그를 먼저** 남기고 수행한다 (P5). `trace_id → proposal_id → order_id → position_id` 체인을 끊지 않는다.
