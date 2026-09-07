"""인터페이스 계약 검증 (P0-3-6).

두 가지를 확인한다:

1. **프로토콜 준수** — 더미 구현체를 프로토콜 타입 변수에 대입한다. 구조가 어긋나면
   pyright 가 정적으로 잡는다 (런타임 isinstance 가 아니라 정적 검사가 목적이다).
2. **스펙 필드 커버리지** — spec §4.2/§4.3/§4.5/§4.6 이 명시한 키가 타입에 전부
   존재하는지 기계적으로 대조한다. `docs/platform/interfaces_v1.md` 의 매핑표가 사람의 눈으로
   하는 대조라면, 이쪽은 CI 가 하는 대조다.
"""

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.analysis.detectors.base import MarketContext, RuleParams, SetupDetector
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.market import Balance, MarketStatus, Quote
from updown.common.domain.order import OrderKind, OrderRequest, OrderResult, OrderStatus
from updown.common.domain.proposal import (
    ApprovedOrder,
    RevisionTrigger,
    RiskPlanRevision,
    TradeProposal,
)
from updown.common.domain.reports import Indicators, KeyLevels, TechnicalReport
from updown.common.domain.setup import TakeProfitStep, TradeSetup
from updown.marketdata.adapter import BrokerAdapter, Capability
from updown.portfolio.state import PortfolioState

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)


# ---------------------------------------------------------------------------
# 1. 프로토콜 준수 (pyright 정적 검사)
# ---------------------------------------------------------------------------


class _DummyBrokerAdapter:
    """`BrokerAdapter` 준수만 확인하는 더미. 본문은 전부 미구현이다."""

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.SPOT, Capability.WS})

    async def get_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        raise NotImplementedError

    async def get_quote(self, instrument: Instrument) -> Quote:
        raise NotImplementedError

    async def get_balance(self) -> Balance:
        raise NotImplementedError

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        raise NotImplementedError

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        raise NotImplementedError

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        raise NotImplementedError


class _DummySetupDetector:
    """`SetupDetector` 준수만 확인하는 더미."""

    @property
    def id(self) -> str:
        return "dummy"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def params(self) -> RuleParams:
        return RuleParams(rule_id="dummy", version="1.0", values={})

    def detect(self, ctx: MarketContext) -> list[TradeSetup]:
        raise NotImplementedError


def test_dummy_adapter_satisfies_broker_adapter_protocol() -> None:
    # 이 대입이 pyright 검사 지점이다. 시그니처가 어긋나면 타입 에러가 난다.
    adapter: BrokerAdapter = _DummyBrokerAdapter()
    assert adapter.capabilities == frozenset({Capability.SPOT, Capability.WS})


def test_dummy_detector_satisfies_setup_detector_protocol() -> None:
    detector: SetupDetector = _DummySetupDetector()
    assert detector.id == "dummy"


# ---------------------------------------------------------------------------
# 2. 스펙 필드 커버리지
# ---------------------------------------------------------------------------


def _field_names(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}  # pyright: ignore[reportArgumentType]


def test_broker_adapter_exposes_spec_4_2_methods() -> None:
    """spec §4.2 가 명시한 메서드 7종."""
    expected = {
        "get_candles",
        "get_quote",
        "get_balance",
        "submit_order",
        "cancel_order",
        "get_order_status",
        "get_market_status",
    }
    assert expected <= set(dir(BrokerAdapter))


def test_trade_setup_covers_spec_4_3_setup_keys() -> None:
    """spec §4.3 `setups[]` 의 키 11종."""
    expected = {
        "setup_type",
        "rule_version",
        "entry_trigger",
        "entry_plan",
        "avg_entry",
        "stop_loss",
        "tp_ladder",
        "stop_policy_hint",
        "rr_ratio",
        "confidence",
        "evidence",
    }
    assert expected <= _field_names(TradeSetup)


def test_technical_report_covers_spec_4_3_keys() -> None:
    """spec §4.3 `TechnicalReport` 최상위 키."""
    expected = {"instrument", "trend", "trend_strength", "indicators", "key_levels", "setups"}
    assert expected <= _field_names(TechnicalReport)


def test_indicators_cover_spec_4_3_keys() -> None:
    """spec §4.3 `indicators` 의 키 10종."""
    expected = {
        "ma20",
        "ma60",
        "ma120",
        "ma200",
        "rsi14",
        "macd",
        "bb",
        "atr14",
        "vwap",
        "volume_ratio",
    }
    assert expected <= _field_names(Indicators)


def test_key_levels_cover_spec_4_3_keys() -> None:
    """spec §4.3 `key_levels` 의 키 5종."""
    expected = {"prev_low", "prev_high", "support", "resistance", "order_blocks"}
    assert expected <= _field_names(KeyLevels)


def test_trade_proposal_covers_spec_4_5_fields() -> None:
    """spec §4.5 `TradeProposal` 의 필드 10종."""
    expected = {
        "instrument",
        "side",
        "entry",
        "stop_loss",
        "take_profit",
        "rr_ratio",
        "bucket",
        "score",
        "evidence_summary",
        "raw_reports",
    }
    assert expected <= _field_names(TradeProposal)


def test_approved_order_covers_spec_4_6_eight_fields() -> None:
    """spec §4.6 `ApprovedOrder` 의 8필드."""
    expected = {
        "proposal_id",
        "total_qty",
        "entry_plan",
        "stop_loss",
        "tp_ladder",
        "stop_policy",
        "valid_until",
        "policy_snapshot",
    }
    assert expected <= _field_names(ApprovedOrder)


def test_portfolio_state_covers_risk_manager_inputs() -> None:
    """spec §4.6 가드레일 판정에 필요한 입력이 전부 있어야 한다."""
    expected = {
        "total_equity_krw",  # 포지션 사이징의 분모
        "daily_realized_pnl_krw",  # 일일 손실 한도 (킬 스위치)
        "open_position_count",  # 최대 동시 포지션
        "by_bucket",  # 버킷 배분 한도
        "is_stale",  # §4.18 — 낡은 값으로 판단하지 않는다
    }
    assert expected <= _field_names(PortfolioState)


def test_portfolio_state_holds_no_targets() -> None:
    """사실/판단 분리 (spec §4.18, plan P-2).

    목표 비중·이탈률은 §4.7 Allocation 의 판단이며 `decision/` 소속이다.
    여기에 target 이 생기면 `portfolio ↔ decision` 순환의 시작이다.
    """
    forbidden = {"target_weight", "target_weights", "drift_pct", "rebalance_proposal"}
    assert not (forbidden & _field_names(PortfolioState))


def test_order_idempotency_key_is_per_order() -> None:
    """spec v1.8 §9 — 멱등키는 승인 단위가 아니라 주문 단위다.

    분할 진입이면 승인 1건에서 주문이 N건 나간다. 승인 단위 키 하나로 내보내면
    브로커가 2·3번 레그를 중복으로 거부한다 (§4.10 "모든 주문에 멱등키").
    """
    order_fields = _field_names(OrderRequest)
    assert {"idempotency_key", "leg_index", "order_kind"} <= order_fields

    approved_fields = _field_names(ApprovedOrder)
    assert "idempotency_root" in approved_fields
    assert "idempotency_key" not in approved_fields, (
        "ApprovedOrder 에 idempotency_key 가 있으면 어느 쪽이 정본인지 다시 모호해진다"
    )


def test_idempotency_key_includes_order_kind_to_avoid_collision() -> None:
    """spec v1.8 §9 — `order_kind` 없이는 진입 0번 레그와 익절 1단계가 같은 키가 된다.

    v1.7 의 파생 규칙 `f"{root}:{leg_index}"` 에 있던 실제 충돌이다.
    """
    root = "ao_001"

    def derive(kind: OrderKind, leg_index: int) -> str:
        return f"{root}:{kind.value}:{leg_index}"

    assert derive(OrderKind.ENTRY, 0) != derive(OrderKind.TAKE_PROFIT, 0)
    keys = {derive(k, i) for k in OrderKind for i in range(3)}
    assert len(keys) == len(OrderKind) * 3, "종류 x 순번 조합의 키가 유일해야 한다"


def test_risk_plan_revision_records_post_approval_reconfirmation() -> None:
    """spec §9.1 — 익절 사다리는 계획이고, 2차는 재평가 후 확정된다.

    재확정을 기록할 곳이 없으면 집행이 `ApprovedOrder` 를 고치게 되고, 그것은
    절대 규칙 #4(손절/익절의 SSoT 는 RiskManager) 위반이다.
    """
    expected = {
        "approved_order_id",
        "revision_no",
        "trigger",
        "stop_loss",  # 반익반본으로 상향된 값
        "tp_ladder",  # 재확정된 남은 익절 계획
        "trace_id",  # §4.14 ID 체인
    }
    assert expected <= _field_names(RiskPlanRevision)
    # 반익반본(§6.9)이 트리거로 표현되어야 한다
    assert RevisionTrigger.FIRST_TP_FILLED in set(RevisionTrigger)


def test_planned_ladder_and_submitted_order_are_separate_types() -> None:
    """spec §9.1 — 계획(tp_ladder)과 실제 제출 주문(OrderRequest)은 섞이지 않는다.

    계획 단계를 미리 주문 행으로 만들어 두면, 재확정으로 값이 달라졌을 때
    원래 계획을 복원할 수 없다.
    """
    # 계획 단계에는 멱등키·브로커 식별자가 없다 — 아직 주문이 아니기 때문이다.
    plan_fields = _field_names(TakeProfitStep)
    assert not ({"idempotency_key", "broker_order_id", "revision_id"} & plan_fields)
    # 반대로 제출 주문은 어느 개정이 인가했는지를 들고 있다.
    assert "revision_id" in _field_names(OrderRequest)


# ---------------------------------------------------------------------------
# 3. 불변식
# ---------------------------------------------------------------------------


def test_timeframe_values_match_backfill_cli_arguments() -> None:
    """plan D-8 — 이 문자열이 P0-8 백필 CLI 의 `--timeframe` 인자와 같아야 한다."""
    # ⭐ **10종으로 늘었다** (사용자 확정 2026-08-18 — 하위 축은 보기 전용).
    #
    # 🔴 이 목록을 고칠 때 **함께 고쳐야 하는 곳**이 있다. 이 테스트가 그것을 잡는다:
    #      marketdata/ingest/timeframes.py   초 단위 표
    #      marketdata/gate/mapping.py        Gate interval 문자열
    #      alembic ck_candles_timeframe      DB 체크 제약
    #      config/backfill.yml               적재 대상 (하위 축은 **넣지 않았다**)
    #
    # ⛔ 진입 축으로 쓰지 않는다 — 5m 폐기 이유가 비용이고, 봉이 짧아지면 그 산수가
    #   더 나빠진다. Gate 의 10,000봉 제한 때문에 30s 는 과거가 3.5일뿐이다.
    assert {tf.value for tf in Timeframe} == {
        "10s",
        "30s",
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "8h",
        "1d",
    }


def test_candle_accepts_utc_aware_timestamp() -> None:
    candle = Candle(
        instrument=BTC,
        timeframe=Timeframe.M5,
        ts=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("105"),
        volume=Decimal("1.5"),
    )
    assert candle.ts.tzinfo is UTC


def test_candle_rejects_naive_timestamp() -> None:
    """spec §12.3 — naive datetime 은 조용히 통과하면 안 된다 (절대 규칙 #7, #8)."""
    with pytest.raises(ValueError, match="UTC aware"):
        Candle(
            instrument=BTC,
            timeframe=Timeframe.M5,
            ts=datetime(2026, 1, 1, 0, 0),  # 의도적으로 naive
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            volume=Decimal("1.5"),
        )


def test_domain_value_objects_are_frozen() -> None:
    """분석이 만든 값을 결정이, 결정이 확정한 값을 집행이 바꾸지 못해야 한다 (원칙 P4)."""
    for cls in (Instrument, Candle, TradeSetup, TradeProposal, ApprovedOrder):
        params = getattr(cls, "__dataclass_params__")  # noqa: B009
        assert params.frozen, f"{cls.__name__} 은 frozen 이어야 한다"
