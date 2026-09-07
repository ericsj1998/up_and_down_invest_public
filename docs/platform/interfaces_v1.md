# 도메인 인터페이스 계약 v1

| 항목 | 값 |
|------|-----|
| 버전 | **v1** |
| 확정일 | 2026-08-03 (P0-3) |
| 근거 | [auto_invest_spec.md](../planning/auto_invest_spec.md) **v1.8** §4.2 · §4.3 · §4.3.1 · §4.5 · §4.6 · §4.10 · §9 · §9.1 · §12.2 · §12.3 · §12.8 |
| 결정 기록 | [Phase00_Foundation_plan.md](../planning/history/Phase00_Foundation_plan.md) D-8, D-11, D-12 |
| 상태 | ✅ **승인 완료** (2026-08-03) — §5 판단 A1~A15 전건 승인, §6 스펙 문제 전건 반영(spec v1.7) |
| 기계 검증 | [tests/test_interfaces.py](../../tests/test_interfaces.py) — 아래 매핑표의 필드 커버리지를 CI 가 재검사한다 |

> **이 문서의 역할**: 스펙 조문과 코드 타입의 **1:1 대응**을 남긴다.
> 구현 본문은 없다 — 산출물은 코드가 아니라 **합의된 계약**이다.
> 확정 후 필드를 추가·삭제하려면 §8 변경 이력에 행을 남긴다.

---

## 1. 파일 배치

계층 순서(`common → marketdata → … → apps`)를 그대로 따른다.

| 파일 | 담는 것 |
|------|---------|
| `common/domain/instrument.py` | `Market` `AssetType` `Currency` `Timeframe` `Bucket` `Side` `Instrument` |
| `common/domain/candle.py` | `Candle` |
| `common/domain/market.py` | `MarketSession` `MarketStatus` `Quote` `Balance` |
| `common/domain/order.py` | `OrderKind` `OrderType` `OrderStatus` `OrderRequest` `OrderResult` |
| `common/domain/structure.py` | `StructureType` `StructureStatus` `PriceRange` `Anchor` `Structure` |
| `common/domain/setup.py` | `EntryTrigger` `TpFollowUpAction` `EvidenceCategory` `EntryLeg` `TakeProfitStep` `StopPolicyHint` `Evidence` `TradeSetup` |
| `common/domain/reports.py` | `TrendDirection` `OrderBlockKind` `MacdValue` `BollingerBands` `Indicators` `OrderBlockLevel` `KeyLevels` `TechnicalReport` |
| `common/domain/proposal.py` | `ProposalStatus` `RejectionReason` `RiskPresetName` `TrailingConfig` `TimeStopConfig` `StopPolicy` `RiskPolicy` `RawReports` `TradeProposal` `ApprovedOrder` `RevisionTrigger` `RiskPlanRevision` `Rejection` |
| `marketdata/adapter.py` | `Capability` `BrokerAdapter` `DerivativesAdapter` |
| `portfolio/state.py` | `BucketExposure` `PortfolioState` |
| `analysis/detectors/base.py` | `ParamValue` `RuleParams` `MarketContext` `SetupDetector` |

**계획과 달라진 배치 1건**: 계획은 `TradeSetup` 을 `proposal.py` 에 뒀으나
`setup.py` 로 분리했다 — 근거는 §6-A(순환 참조).

---

## 2. 스펙 → 타입 필드 매핑

> 대조 방법: 왼쪽 두 열이 스펙 원문, 오른쪽이 코드다. **스펙에 있는데 코드에 없으면 결함**이고,
> 코드에만 있는 것은 "출처" 열에 근거 조문을 적었다.

### 2.1 §4.2 `BrokerAdapter` — 메서드 7종

| 스펙 §4.2 시그니처 | 구현 | 반환 타입 | 확인 |
|---|---|---|---|
| `get_candles(inst, timeframe, start, end) -> list[Candle]` | `BrokerAdapter.get_candles` | `list[Candle]` | ☐ |
| `get_quote(inst) -> Quote` | `BrokerAdapter.get_quote` | `Quote` | ☐ |
| `get_balance() -> Balance` | `BrokerAdapter.get_balance` | `Balance` | ☐ |
| `submit_order(order: OrderRequest) -> OrderResult` *(멱등키 필수)* | `BrokerAdapter.submit_order` | `OrderResult` | ☐ |
| `cancel_order(order_id) -> OrderResult` | `BrokerAdapter.cancel_order` | `OrderResult` | ☐ |
| `get_order_status(order_id) -> OrderStatus` | `BrokerAdapter.get_order_status` | `OrderStatus` | ☐ |
| `get_market_status(inst) -> MarketStatus` | `BrokerAdapter.get_market_status` | `MarketStatus` | ☐ |

**모든 메서드가 `async`** — 근거는 §5-A3.

### 2.2 §4.2 `capabilities`

| 스펙 값 | `Capability` 멤버 | 확인 |
|---|---|---|
| `spot` | `SPOT` | ☐ |
| `derivatives` | `DERIVATIVES` | ☐ |
| `leverage` | `LEVERAGE` | ☐ |
| `short` | `SHORT` | ☐ |
| `ws` | `WS` | ☐ |
| `conditional_orders` | `CONDITIONAL_ORDERS` | ☐ |
| `funding` | `FUNDING` | ☐ |

업비트 = `{SPOT, WS}` (스펙 명시) → P0-7-6 DoD 로 검증.

`DerivativesAdapter` 4종(`set_leverage` / `get_margin_info` / `get_liquidation_price` /
`get_funding_rate`)도 스펙 그대로 두었다. **자리만**이며 현 Phase 범위 밖이다 (§12.8).

### 2.3 §4.3 `TechnicalReport` — 최상위

| 스펙 §4.3 키 | `TechnicalReport` 필드 | 타입 | 확인 |
|---|---|---|---|
| `instrument` | `instrument` | `Instrument` | ☐ |
| `trend` (`UP\|DOWN\|SIDEWAYS`) | `trend` | `TrendDirection` | ☐ |
| `trend_strength` (0.0~1.0) | `trend_strength` | `float` | ☐ |
| `indicators` | `indicators` | `Indicators` | ☐ |
| `key_levels` | `key_levels` | `KeyLevels` | ☐ |
| `setups` | `setups` | `tuple[TradeSetup, ...]` | ☐ |
| — | `timeframe` | `Timeframe` | 출처: §4.3 **입력** `timeframe(들)` |
| — | `as_of` | `datetime` | 출처: §4.3 입력 "분석 시점" · §9 `analysis_reports.created_at` |

### 2.4 §4.3 `indicators`

| 스펙 키 | 필드 | 타입 | 확인 |
|---|---|---|---|
| `ma20` | `ma20` | `Decimal \| None` | ☐ |
| `ma60` | `ma60` | `Decimal \| None` | ☐ |
| `ma120` | `ma120` | `Decimal \| None` | ☐ |
| `ma200` | `ma200` | `Decimal \| None` | ☐ |
| `rsi14` | `rsi14` | `float \| None` | ☐ |
| `macd` | `macd` | `MacdValue \| None` (`line` / `signal` / `histogram`) | ☐ |
| `bb` | `bb` | `BollingerBands \| None` (`upper` / `middle` / `lower`) | ☐ |
| `atr14` | `atr14` | `Decimal \| None` | ☐ |
| `vwap` | `vwap` | `Decimal \| None` | ☐ |
| `volume_ratio` | `volume_ratio` | `float \| None` | ☐ |

전 필드가 Optional 인 근거는 §5-A7.

### 2.5 §4.3 `key_levels`

| 스펙 키 | 필드 | 타입 | 확인 |
|---|---|---|---|
| `prev_low` | `prev_low` | `Decimal \| None` | ☐ |
| `prev_high` | `prev_high` | `Decimal \| None` | ☐ |
| `support` | `support` | `tuple[Decimal, ...]` | ☐ |
| `resistance` | `resistance` | `tuple[Decimal, ...]` | ☐ |
| `order_blocks[].range` | `order_blocks[].price_range` | `PriceRange(low, high)` | ☐ |
| `order_blocks[].type` (`demand\|supply`) | `order_blocks[].kind` | `OrderBlockKind` | ☐ |
| `order_blocks[].volume_score` | `order_blocks[].volume_score` | `float` | ☐ |
| — | `order_blocks[].structure_id` | `str \| None` | 출처: §4.13 `evidence_refs` · §9 `structures.id` |

### 2.6 §4.3 `setups[]` → `TradeSetup` ⭐

| 스펙 키 | 필드 | 타입 | 확인 |
|---|---|---|---|
| `setup_type` | `setup_type` | `str` (열거형 아님 — §5-A5) | ☐ |
| `rule_version` | `rule_version` | `str` (`order_block@1.2`) | ☐ |
| `entry_trigger` (`TOUCH\|CLOSE_CONFIRM`) | `entry_trigger` | `EntryTrigger` | ☐ |
| `entry_plan[].price` | `entry_plan[].price` | `Decimal` | ☐ |
| `entry_plan[].ratio` | `entry_plan[].ratio` | `Decimal` | ☐ |
| `avg_entry` | `avg_entry` | `Decimal` | ☐ |
| `stop_loss` | `stop_loss` | `Decimal` | ☐ |
| `tp_ladder[].price` | `tp_ladder[].price` | `Decimal` | ☐ |
| `tp_ladder[].ratio` | `tp_ladder[].ratio` | `Decimal` | ☐ |
| `tp_ladder[].then` (`MOVE_STOP_TO_BREAKEVEN`) | `tp_ladder[].then` | `TpFollowUpAction \| None` | ☐ |
| `stop_policy_hint.never_lower` | `stop_policy_hint.never_lower` | `bool` | ☐ |
| `stop_policy_hint.trailing` | `stop_policy_hint.trailing` | `bool \| None` (§5-A13) | ☐ |
| `rr_ratio` | `rr_ratio` | `Decimal` | ☐ |
| `confidence` (0~1) | `confidence` | `float` | ☐ |
| `evidence[].rule` | `evidence[].rule` | `str` | ☐ |
| `evidence[].detail` | `evidence[].detail` | `str` | ☐ |
| `evidence[].weight` | `evidence[].weight` | `float` | ☐ |
| — | `evidence[].category` | `EvidenceCategory` | 출처: **§5.5 카테고리당 1표** |
| — | `evidence[].refs` | `tuple[str, ...]` | 출처: **§4.13 `evidence_refs`** |

`EvidenceCategory` = 추세 / 모멘텀 / 구조물 / 거래량·유동성 / 펀더멘탈 / 시장 국면 (§5.5 원문 그대로).

### 2.7 §4.3.1 셋업 탐지 플러그인

| 스펙 §4.3.1 | 구현 | 확인 |
|---|---|---|
| `SetupDetector.id: str` | `SetupDetector.id` (property) | ☐ |
| `SetupDetector.version: str` | `SetupDetector.version` (property) | ☐ |
| `SetupDetector.params: RuleParams` | `SetupDetector.params` (property) | ☐ |
| `detect(self, ctx: MarketContext) -> list[TradeSetup]` | 동일 | ☐ |
| `MarketContext` — 멀티 TF 캔들 | `MarketContext.candles: Mapping[Timeframe, Sequence[Candle]]` | ☐ |
| `MarketContext` — 기 계산 공용 지표 | `MarketContext.indicators: Mapping[Timeframe, Indicators]` | ☐ |
| `MarketContext` — 기 탐지 구조물 | `MarketContext.structures: Sequence[Structure]` | ☐ |
| `RuleParams` — 설정에서 주입 | `RuleParams(rule_id, version, values: Mapping[str, ParamValue])` | ☐ |
| 구조물 생애주기 `active\|invalidated\|flipped` | `StructureStatus` | ☐ |

`MarketContext.as_of` 는 **미래 참조 금지**(§4.11)의 경계선이자 결정론(P1)의 시각 원천이다.

### 2.8 §4.5 `TradeProposal`

| 스펙 §4.5 필드 | 구현 | 타입 | 확인 |
|---|---|---|---|
| `instrument` | `instrument` | `Instrument` | ☐ |
| `side` | `side` | `Side` | ☐ |
| `entry` | `entry` | `Decimal` | ☐ |
| `stop_loss` | `stop_loss` | `Decimal` | ☐ |
| `take_profit` | `take_profit` | `Decimal` | ☐ |
| `rr_ratio` | `rr_ratio` | `Decimal` | ☐ |
| `bucket` | `bucket` | `Bucket` | ☐ |
| `score` | `score` | `float` | ☐ |
| `evidence_summary` (AI) | `evidence_summary` | `str \| None` — P7 OFF 시 None | ☐ |
| `raw_reports` | `raw_reports` | `RawReports(technical=...)` | ☐ |
| — | `proposal_id` `trace_id` `evidence` `valid_until` `status` | — | 출처: §9 `trade_proposals` · §4.14 ID 체인 |

### 2.9 §4.6 `ApprovedOrder` — 8필드 + 영속화 4필드

| 스펙 §4.6 필드 | 구현 | 타입 | 확인 |
|---|---|---|---|
| `proposal_id` | `proposal_id` | `str` | ☐ |
| `total_qty` | `total_qty` | `Decimal` | ☐ |
| `entry_plan[]` | `entry_plan` | `tuple[EntryLeg, ...]` | ☐ |
| `stop_loss` | `stop_loss` | `Decimal` | ☐ |
| `tp_ladder[]` | `tp_ladder` | `tuple[TakeProfitStep, ...]` | ☐ |
| `stop_policy` | `stop_policy` | `StopPolicy` | ☐ |
| `valid_until` | `valid_until` | `datetime` | ☐ |
| `policy_snapshot` | `policy_snapshot` | `RiskPolicy` (전문 스냅샷) | ☐ |
| — | `approved_order_id` `user_id` `idempotency_root` `status` | — | 출처: §9 `approved_orders` (v1.7) |

`StopPolicy` 구성 (§6.9 포지션 관리 공통 룰):

| §6.9 룰 | 필드 | 확인 |
|---|---|---|
| 스탑 불변 원칙 | `never_lower: bool` (항상 True) | ☐ |
| 반익반본 | `move_stop_to_breakeven_after_first_tp: bool` | ☐ |
| 트레일링 스탑 | `trailing: TrailingConfig \| None` (`atr_multiple`) | ☐ |
| 타임 스탑 | `time_stop: TimeStopConfig \| None` (`reduce_tp_after_days` N / `reduced_tp_pct` / `exit_after_days` M) | ☐ |
| 3분할 진입 | `ApprovedOrder.entry_plan` 의 레그 비율 | ☐ |

### 2.10 §4.6 `RiskPolicy` + 프리셋

| 스펙 §4.6 프리셋 표 열 | 필드 | 확인 |
|---|---|---|
| 회당 리스크 | `risk_pct: Decimal` | ☐ |
| 최소 RR | `min_rr: Decimal` | ☐ |
| 동시 포지션 | `max_positions: int` | ☐ |
| 일일 손실 한도 | `daily_loss_limit_pct: Decimal` | ☐ |
| 프리셋 이름 (보수/표준/공격) | `RiskPresetName.CONSERVATIVE / STANDARD / AGGRESSIVE` | ☐ |
| (§6.9 트레일링 단타 활성/장투 비활성) | `trailing_enabled: bool` | ☐ |
| (§9 `risk_policies` 키) | `user_id: str` `bucket: Bucket` | ☐ |

**수치(0.5%/2.0/3/-2% …)는 코드에 넣지 않았다** — 근거 §5-A8.

### 2.11 §4.6 `Rejection { reason_code }` — 초안

| 코드 | 근거 조문 | 확인 |
|---|---|---|
| `ACTIVE_EXIT_SIGNAL` | §5.4 #1 청산 > 진입 | ☐ |
| `COUNTER_TREND` | §5.4 #2 추세 게이트 | ☐ |
| `REGIME_BLOCKED` | §5.4 #3 · §4.15 | ☐ |
| `RR_BELOW_MINIMUM` | §4.6 | ☐ |
| `ON_WATCHLIST` | §4.6 브레이커 ① | ☐ |
| `RULE_DISABLED` | §4.6 브레이커 ② | ☐ |
| `DAILY_LOSS_LIMIT_REACHED` | §4.6 킬 스위치 | ☐ |
| `MAX_POSITIONS_REACHED` | §4.6 | ☐ |
| `BELOW_MIN_ORDER_AMOUNT` | §12.2 | ☐ |
| `THESIS_INVALIDATED` | §4.4 논지 무효화 | ☐ |

계획이 요구한 5종(추세 역행·국면 차단·RR 미달·관찰 목록·최소 주문금액)은 전부 포함했고,
나머지 5종은 같은 조문들이 명시한 거부 경로라 함께 넣었다.

### 2.12 §4.6 `PortfolioState` — RiskManager 입력

§4.6 이 입력으로 명시하고, plan D-6 이 `portfolio` 를 `analysis` 아래 둔 근거인 타입이다.
**사실만 담는다** — 목표 비중·이탈률은 §4.7 Allocation 의 판단이며 `decision/` 소속이다 (plan P-2).

| 근거 조문 | 필요한 것 | 필드 | 확인 |
|---|---|---|---|
| §4.6 포지션 사이징 | 계좌 총 평가금액 (분모) | `total_equity_krw: Decimal` | ☐ |
| §4.6 일일 손실 한도 (킬 스위치) | 당일 실현 손익 | `daily_realized_pnl_krw: Decimal` | ☐ |
| §4.6 최대 동시 포지션 | 열린 포지션 수 | `open_position_count: int` | ☐ |
| §4.6 버킷별 배분 한도 | 버킷별 현재 노출 | `by_bucket: Mapping[Bucket, BucketExposure]` | ☐ |
| §4.18 실현/미실현 분리 | | `realized_pnl_krw` `unrealized_pnl_krw` | ☐ |
| §4.18 **환손익 분리** | | `fx_pnl_krw: Decimal` | ☐ |
| §4.18 계좌별 배분 · §4.19 페이퍼 | 계좌별 잔고 | `by_account: tuple[Balance, ...]` | ☐ |
| §4.18 "오래된 값을 최신처럼 보여주지 않는다" | staleness | `is_stale: bool` | ☐ |
| §9 `portfolio_snapshots` | 스냅샷 키 | `user_id` `as_of` `cash_krw` | ☐ |

`BucketExposure` = `bucket` / `value_krw` / `position_count`.

**의도적으로 없는 것**: `target_weight`·`drift_pct` (→ §4.7 `decision/`),
TWR (→ 대시보드 파생 지표, `allocation_ledger` 재료로 orchestration 산출).
`tests/test_interfaces.py::test_portfolio_state_holds_no_targets` 가 이 경계를 강제한다.

### 2.13 §4.10 주문 생애주기

| 스펙 §4.10 `OrderEvent` | `OrderStatus` 멤버 | 확인 |
|---|---|---|
| `PENDING` | `PENDING` | ☐ |
| `WATCHING` | `WATCHING` | ☐ |
| `SUBMITTED` | `SUBMITTED` | ☐ |
| `PARTIALLY_FILLED` | `PARTIALLY_FILLED` | ☐ |
| `FILLED` | `FILLED` | ☐ |
| `EXPIRED` | `EXPIRED` | ☐ |
| `CANCELLED` | `CANCELLED` | ☐ |
| `FAILED` | `FAILED` | ☐ |

`OrderRequest`: `instrument` `side` **`order_kind`** `order_type` `quantity` `price`
**`idempotency_key`(필수)** `approved_order_id` `leg_index` `revision_id`.
`OrderResult`: `broker_order_id` `idempotency_key` `status` `filled_quantity` `average_price`
`ts` `reason`.

**멱등키는 주문 단위다** (spec v1.8 §9):
`idempotency_key = f"{ApprovedOrder.idempotency_root}:{order_kind}:{leg_index}"`.
승인 단위 키 하나로 N개 레그를 내보내면 브로커가 2·3번 레그를 중복으로 거부하고,
`order_kind` 가 빠지면 **진입 0번 레그와 익절 1단계가 같은 키**가 된다.

`OrderKind` = `entry` / `take_profit` / `stop_loss` / `close`.
`Side`(매수·매도 방향)와 다르다 — 익절과 강제 청산은 둘 다 `SELL` 이지만 목적·손익 귀속·
멱등키가 갈린다.

### 2.13.1 §9.1 진입 ≠ 익절 — 계획과 실제의 분리 ⭐

`entry_plan[]` 과 `tp_ladder[]` 는 둘 다 배열이지만 집행이 대칭이 아니다.

| | 진입 | 익절 |
|---|---|---|
| 주문 제출 | 가격이 각 레그에 닿을 때 **순차 제출** | **1차만 제출**, 2차 이후는 미제출 |
| 값의 변경 | 없음 | **있다** — 1차 체결 후 재확정 |

| 흐름 | 담는 곳 | 확인 |
|---|---|---|
| 익절 **계획** (미래 목표 + `then` 액션) | `ApprovedOrder.tp_ladder` | ☐ |
| 1차 체결 후 **재확정** (본절 이동 + 2차 익절선 재확정) | **`RiskPlanRevision`** | ☐ |
| 재확정을 촉발한 사건 | `RevisionTrigger.FIRST_TP_FILLED` 외 5종 | ☐ |
| 실제로 나간 주문만 | `OrderRequest` (계획 단계는 행이 없다) | ☐ |
| 어느 개정이 이 주문을 인가했는지 | `OrderRequest.revision_id` | ☐ |

`RiskPlanRevision`: `revision_id` `approved_order_id` `position_id` `revision_no` `trigger`
`stop_loss` `tp_ladder` `reason` `trace_id` `decided_at`.

`RevisionTrigger` = `first_tp_filled`(§6.9 반익반본) / `trailing`(§6.9) / `time_stop`(§6.9)
/ `gap_open`(§7) / `transition`(§4.8 유형 B) / `manual`(§4.6 수동 모드).

**이 타입이 있어야 `ApprovedOrder` 가 frozen 일 수 있다.** 집행이 값을 바꿔야 할 때
승인 객체를 고치는 것이 아니라, RiskManager 가 새 개정을 발행하고 집행은 그것을 읽는다
(§5.1, 절대 규칙 #4). `stop_loss` 가 직전 개정보다 낮을 수 없다는 검증도 이 이력이 근거다
— 현재값 스칼라만으로는 하향 여부를 판정할 수 없다 (절대 규칙 #3).

### 2.14 §9 테이블 ↔ 도메인 타입

| §9 테이블 | 대응 타입 | 상태 |
|---|---|---|
| `instruments` | `Instrument` | ✅ 이번에 확정 |
| `candles` | `Candle` | ✅ |
| `structures` | `Structure` | ✅ |
| `trade_proposals` | `TradeProposal` | ✅ |
| `risk_policies` | `RiskPolicy` | ✅ |
| `approved_orders` | `ApprovedOrder` | ✅ (§6-B 반영) |
| `risk_plan_revisions` | `RiskPlanRevision` | ✅ v1.8 신설 (§6-G) |
| `orders` | `OrderRequest` / `OrderResult` | ✅ (§6-D·§6-G 반영) |
| `account_balances` | `Balance` | ✅ |
| `analysis_reports` | `TechnicalReport` (payload) | ✅ |
| `portfolio_snapshots` | `PortfolioState` | ✅ 결정 ① 반영 |
| `users` `broker_credentials` | — | P3-8 (인증) |
| `candle_quality_issues` | — | P0-4/P0-8 (ORM 로만) |
| `positions` `transitions` | — | P2 |
| `allocation_ledger` | — | P3 |
| `event_logs` `notifications` | — | P0-6 / P2-5 |
| `backtest_runs` | — | P1-8 |

**총 20개** — P0-4 DoD 2번의 "17개"는 오기였고 정정했다 (§6-F).
v1.8 의 `risk_plan_revisions` 를 포함한 수치다.

### 2.15 횡단 규칙 반영 위치

| 규칙 | 반영 위치 | 확인 |
|---|---|---|
| §12.3 저장은 UTC | `Candle.__post_init__` 가드 · 전 `datetime` 필드 UTC 규약 | ☐ |
| §12.2 호가단위 라운딩 | `marketdata` 레이어 책임으로 문서화. `OrderRequest.quantity`/`price` 는 **라운딩 완료값** | ☐ |
| §12.8 롱 온리 | `Side.SELL` 은 청산만 · `OrderBlockKind.SUPPLY` 는 청산·회피 근거 · `DerivativesAdapter` 는 자리만 | ☐ |
| §4.10 멱등키 필수 | `OrderRequest.idempotency_key` 기본값 없음 | ☐ |
| §4.14 ID 체인 | `TradeProposal.trace_id` → `proposal_id` → `OrderRequest.approved_order_id` | ☐ |
| §5.3 AI 경계 | `evidence_summary` 만 `str \| None` — 숫자 필드는 전부 계산식 출력 | ☐ |
| D-8 Timeframe 5종 | `Timeframe` = `5m/15m/1h/4h/1d` (테스트로 고정) | ☐ |

---

## 3. 의도적 제외 — 지금 고정하지 않은 계약

원칙: **가까운 소비자가 있는 계약만 지금 고정한다** (plan D-11).

| 타입 | 스펙 | 도입 시점 | 이유 |
|---|---|---|---|
| `FundamentalReport` | §4.4 | **P3-1** | plan D-11 확정. DART 응답 구조·적정가 모델(D3-2)이 미정이라 지금 고정하면 어긋난 채 방치된다 |
| `TrendState` / `TrendTransition` | §4.16 | P1 | Trend Service 구현과 함께 |
| `MarketRegime` | §4.15 | P1~P2 | |
| `AllocationState` / `RebalanceProposal` | §4.7 | P3 | |
| `TransitionDecision` | §4.8 | P3 | |
| `BacktestReport` | §4.11 | P1-8 | |
| `ChartSpec` | §4.13 | P1-11 | |
| `Position` / `OrderEvent` | §4.10 | P2 | |
| `NotificationEvent` | §4.12 | P2-5 | |

> ✅ **`PortfolioState` 는 이번에 포함했다** (결정 ①, 2026-08-03). P0-3 계획 목록에는
> 없었지만 §4.6 이 RiskManager 입력으로 명시하고 **plan D-6 이 `portfolio` 를 analysis
> 아래 둔 근거 자체가 이 타입**이라, 계층 근거와 코드가 어긋난 상태로 두지 않는다.
> 매핑표는 §2.12.

---

## 4. 설계 원칙 — 왜 이렇게 생겼는가

**전부 frozen dataclass다.** 분석이 만든 값을 결정이 바꾸고, 결정이 확정한 값을 집행이
바꾸는 사고를 타입 레벨에서 막는다 (P4, §5.1). 컬렉션이 `list` 가 아니라 `tuple` 인 이유도
같다 — frozen dataclass 라도 list 필드는 내용이 바뀐다.

**세 계약이 P4 의 경계선이다.**

```
TradeSetup / TechnicalReport   분석의 제안   숫자가 있지만 확정이 아니다
        ↓
TradeProposal                  취합된 제안   AI 는 evidence_summary 만 건드린다
        ↓
ApprovedOrder                  결정의 확정   손절/익절의 SSoT
        ↓
OrderRequest                   집행의 입력   값을 바꿀 권한이 없다
```

---

## 5. 스펙에 답이 없어 판단한 것 — ✅ **전건 승인** (2026-08-03)

| # | 항목 | 판단 | 근거 |
|---|---|---|---|
| **A1** | 도메인 모델 표현 | **frozen dataclass** (pydantic 아님) | 내부 값 객체이지 I/O 스키마가 아니다. pydantic 의 런타임 강제 변환은 값을 **조용히 바꿀** 수 있어 결정론 코어(P1)와 상충한다. API 직렬화 DTO 는 필요할 때 따로 만든다 |
| **A2** | 숫자 타입 기준 | **가격·수량·금액·비율(수량 환산) = `Decimal`** / **무차원 점수 = `float`** | 부동소수 오차가 체결 수량과 손절가에 들어가면 안 된다. `confidence` `score` `weight` `rsi14` `trend_strength` `volume_ratio` 는 순위·표시용이라 float |
| **A3** | `BrokerAdapter` 비동기 | **전 메서드 `async`** | 런타임 전체가 asyncio (FastAPI · httpx async · `redis.asyncio` · psycopg async · AsyncIOScheduler). `BacktestAdapter` 처럼 I/O 없는 구현도 같은 시그니처를 지켜야 전략 코드가 백테스트와 실거래에서 동일하게 돈다 (P3) |
| **A4** | `TradeSetup` 파일 위치 | 계획의 `proposal.py` 대신 **`setup.py` 신설** | 순환 참조 — §6-A |
| **A5** | `setup_type` 은 `str`, `StructureType` 은 열거형 | 비대칭 의도 | 셋업은 플러그인이라 "파일 1개 + 레지스트리 1줄"로 늘어야 한다(§4.3.1) — 열거형이면 공용 타입을 매번 고쳐야 한다. 구조물은 §9 가 DB 컬럼 값을 못박으므로 열거형 |
| **A6** | `Market` · `Currency` 열거형화 | `KRX/NASDAQ/NYSE/UPBIT`, `KRW/USD` | 집합이 작고 느리게 변한다. 문자열이면 오타가 런타임까지 산다. 추가는 한 줄 |
| **A7** | `Indicators` 전 필드 Optional | `\| None` | 상장 3개월짜리 코인에 200일선은 **존재하지 않는다**. 0 이나 직전값으로 채우면 추세 판정(§4.16)이 조용히 틀린 답을 낸다 |
| **A8** | 프리셋 수치 하드코딩 안 함 | 이름만 열거형, 값은 주입 | §4.6 "수치는 초기값 — 관리자 설정 + 백테스트로 조정" + CLAUDE.md "임계값은 코드에 박지 않는다". 스펙 표는 **YAML 초기값**으로 옮긴다 |
| **A9** | `Candle.__post_init__` 런타임 가드 | UTC 아니면 `ValueError` | ✅ **승인** — "타입 계약의 문지기"로 확정. P0-3 의 "구현 금지"는 **비즈니스 로직**을 막는 것이며 안전 가드는 해당하지 않는다. 파이썬 타입으로 "UTC aware"를 표현할 수 없고, naive 가 조용히 통과하면 §12.3·절대 규칙 #7 이 무너진다 |
| **A10** | `Evidence.category` 추가 | `EvidenceCategory` | §4.3 JSON 에는 없지만 **§5.5 카테고리당 1표**가 카테고리 없이는 계산 불가 |
| **A11** | `Evidence.refs` 추가 | `tuple[str, ...]` | §4.13 `evidence_refs` — 근거↔차트 하이라이트 연결 |
| **A12** | `TechnicalReport.timeframe` / `as_of` 추가 | | §4.3 **입력**에 있고 §9 `analysis_reports` 가 요구한다. TF 를 모르는 리포트는 쓸 수 없다 |
| **A13** | `StopPolicyHint.trailing` 을 `bool \| None` 로 | 파라미터 없음 | §6.9 가 트레일링 **파라미터를 RiskManager 소유**로 못박는다. 분석은 "권한다/아니다" 의견만 낸다 |
| **A14** | `ProposalStatus` 값 집합 | `pending/approved/rejected/expired` **초안** | §9 `status` 의 값 집합을 스펙이 안 정했다. P0-4 에서 DB 제약 걸 때 확정 |
| **A15** | `MarketSession` 값 집합 | `regular/pre_open/closed/always_open/vi/sidecar/circuit_breaker/halted` | §4.2 "정규장/VI/사이드카/휴장" + §7 의 거래정지·코인 24시간 장. bool 이 아닌 이유: VI 는 해제 후 재분석, 거래정지는 포지션 동결 — **대응이 다르다** |

---

## 6. 발견한 스펙 문제 — ✅ **전건 반영 완료** (spec v1.7, 2026-08-03)

### A. `TradeSetup` 배치가 순환 참조를 만든다 *(해결됨)*

§4.3 `TechnicalReport` 가 `setups[]` 를 담고, §4.5 `TradeProposal` 이 `raw_reports` 로
`TechnicalReport` 를 담는다. 계획대로 `TradeSetup` 을 `proposal.py` 에 두면
`proposal → reports → proposal` 순환이다.
→ `setup.py` 를 최하단으로 분리해 `setup → reports → proposal` 단방향으로 해결했다.

### B. §9 `approved_orders` 의 평면 컬럼이 사다리를 담지 못한다 *(해결됨 — spec v1.7)*

기존 스키마는 `qty, entry, stop, take` 로 `entry`·`take` 가 **스칼라 한 개**였는데,
§4.6 은 `entry_plan[]`(3분할)과 `tp_ladder[]`(다단)를 요구한다. 담을 자리가 없었고
`policy_snapshot` 컬럼도 아예 없었다.

→ spec v1.7 에서 교체:

```
approved_orders(id, proposal_id, user_id, total_qty, entry_plan_json, stop_loss, tp_ladder_json,
                stop_policy_json, policy_snapshot_json, idempotency_root, valid_until, status)
```

`_json` 접미사는 §9 의 기존 관례(`evidence_json`, `decision_json`, `by_bucket_json`)를 따랐다.
**`stop_loss` 만 스칼라로 남긴 것은 의도다** — 확정된 손절가는 단일 값이고 그것이 SSoT 다 (§5.1).

### C. §4.5 와 §9 의 필드명이 다르다 *(해결됨 — spec v1.7)*

§4.5 는 `stop_loss`/`take_profit`, §9 는 `stop`/`take` 였다.
→ spec v1.7 에서 **§9 를 `stop_loss`/`take_profit` 으로 통일**했다
(`trade_proposals`, `approved_orders`, `positions` 3개 테이블). 도메인 타입은 §4.5 표기 그대로다.

### D. 멱등키가 `approved_orders` 에 있으면 분할 진입이 깨진다 ⭐ *(해결됨 — spec v1.7)*

§9 는 `idempotency_key` 를 `approved_orders` 에 두고 `orders` 에는 두지 않았다.
그런데 §4.10 은 "**모든 주문에** 클라이언트 생성 `idempotency_key`"를 요구하고,
3분할 진입이면 하나의 `ApprovedOrder` 에서 **주문이 3건** 나간다.
키가 승인 단위로 하나뿐이면 브로커가 2·3번 레그를 중복으로 보고 거부한다.

→ spec v1.7 에서 정본을 `orders` 로 옮겼다:

```
orders(id, approved_order_id, leg_index, broker_order_id, idempotency_key, status,
       filled_qty, avg_price, ts)
```

- `orders.idempotency_key` — **UNIQUE, 주문 1건당 1개**가 정본
- `approved_orders.idempotency_root` — 파생 기준값 (이름을 바꿔 모호함을 제거)
- 파생 규칙: `idempotency_key = f"{idempotency_root}:{leg_index}"`
- `leg_index` — `entry_plan`/`tp_ladder` 내 순번

코드도 함께 맞췄다: `ApprovedOrder.idempotency_key` → `idempotency_root`,
`OrderRequest` 에 `leg_index` 추가.
`tests/test_interfaces.py::test_order_idempotency_key_is_per_leg` 가 재발을 막는다.

### E. §4.3 `evidence` 에 카테고리가 없어 §5.5 를 계산할 수 없다 *(해결됨)*

§5.5 는 카테고리당 1표를 강제하는데 §4.3 의 `evidence` 스키마에는 카테고리 필드가 없다.
→ `Evidence.category` 를 추가했다 (A10).

### G. 익절 사다리의 **순차·재평가 흐름**을 담을 곳이 없었다 ⭐ *(해결됨 — spec v1.8)*

§6.9 와 §4.10 은 "1차 익절 체결 즉시 스탑을 본절로 상향"을 요구하고, 2차 익절선은
그 시점에 시장 방향성을 다시 봐서 재확정된다. 즉 **`tp_ladder` 는 계획이고 실제 주문은
1차만 나간다.** 그런데:

1. `orders` 에 **주문 종류 구분이 없어** v1.7 의 파생 규칙 `f"{root}:{leg_index}"` 로는
   **진입 0번 레그와 익절 1단계가 같은 멱등키**가 됐다 — v1.7 이 만든 실제 충돌이다
2. `orders` 에 **요청가 컬럼이 없어** 재확정으로 계획과 달라진 값을 대조할 수 없었다
3. **재확정 결정 자체를 저장할 테이블이 없었다.** `positions.stop_loss` 는 현재값
   스칼라뿐이라, 스탑 하향 금지(절대 규칙 #3) 검증조차 불가능했다.
   스펙이 승인 후 값 변경을 요구하는 경로는 최소 5개다 — 반익반본·트레일링·타임
   스탑(§6.9), 갭 오픈(§7), 유형 B 전환(§4.8)

→ spec v1.8 에서 셋 다 해소:

- **§9.1 신설** — 진입/익절 집행 비대칭 표 + 5단계 흐름 명문화
- **`risk_plan_revisions` 테이블 신설** (append-only) — `RiskPlanRevision` / `RevisionTrigger`
- `orders` 에 `order_kind` · `requested_price` · `requested_qty` · `revision_id` 추가.
  멱등키 = `f"{root}:{order_kind}:{leg_index}"`

테스트 4건이 재발을 막는다 (`test_idempotency_key_includes_order_kind_to_avoid_collision`,
`test_risk_plan_revision_records_post_approval_reconfirmation`,
`test_planned_ladder_and_submitted_order_are_separate_types`, `test_order_idempotency_key_is_per_order`).

### F. P0-4 DoD 의 "§9 17개 테이블"은 실제 **20개**다 *(해결됨)*

§9 코드블록을 세면 `users … backtest_runs` 로 20개다 (v1.6 `candle_quality_issues` +
v1.8 `risk_plan_revisions` 포함).
→ Phase00 의 P0-4 DoD 2번을 **20개 + 테이블명 전량 열거**로 고쳤다. 다음에 세지 않아도 되게
이름을 다 적었다.

---

## 7. 변경 규약

1. 필드 **추가·삭제·타입 변경**은 §8 변경 이력에 행을 남긴다. 버전은 호환 깨짐이 있을 때만 올린다
2. 스펙 조문이 근거인 필드를 바꿀 때는 **스펙을 먼저 고친다.** 코드가 앞서면 SSoT 가 뒤집힌다
3. §2 매핑표에 없는 필드를 추가하려면 "출처" 열에 근거 조문을 적는다. 근거가 없으면 넣지 않는다
4. `tests/test_interfaces.py` 의 필드 커버리지 테스트를 함께 갱신한다 — 문서만 고치면 CI 가 잡지 못한다

---

## 8. 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2026-08-03 | v1 (리뷰 대기) | 최초 확정. §4.2/§4.3/§4.3.1/§4.5/§4.6/§4.10/§9 계약 정의. 판단 15건(A1~A15)·스펙 문제 6건(A~F) 기록 |
| 2026-08-03 | **v1 (승인)** | A1~A15 전건 승인. 결정 ① `PortfolioState` 추가(`portfolio/state.py`, §2.12) · 결정 ② `Candle` UTC 가드 확정. 스펙 문제 B·C·D 를 spec v1.7 로 반영하고 코드 정합: `ApprovedOrder.idempotency_key` → `idempotency_root`, `OrderRequest.leg_index` 추가. F 는 Phase00 P0-4 DoD 정정 |
| 2026-08-03 | v1 | **§6-G 반영 (spec v1.8)** — 익절 사다리의 순차·재평가 흐름. `RiskPlanRevision` / `RevisionTrigger` / `OrderKind` 추가, `OrderRequest` 에 `order_kind`·`revision_id` 추가. **v1.7 의 멱등키 파생 규칙에 있던 진입/익절 키 충돌을 수정**했다 (`order_kind` 포함). 계약 변경이지만 v1 확정 당일의 결함 수정이므로 버전은 올리지 않는다 |
