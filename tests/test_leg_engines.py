"""외부 다리 엔진 접점 — 등록부 · 증거금 양보 · 브레이크 격리 · 다리 저장 · 펀드 종목 줄."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api.rebalancer import engine_row
from updown.decision.allocation import Basket, as_members
from updown.orchestration import leg_engines
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.orchestration.rebalancer.legs import FundLeg
from updown.portfolio.performance import TwrLedger


class FakeEngine:
    """엔진 면만 갖춘 가짜 — 실현 누계 · 양보 기록."""

    def __init__(self, symbol: str, attribution: str) -> None:
        self._symbol = symbol
        self.attribution = attribution
        self.findings: list[str] = []
        self.pnl = Decimal(0)
        self.asked: list[Decimal] = []
        self.budget: Decimal | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    def realized(self) -> Decimal:
        return self.pnl

    def realized_of(self, attributions: frozenset[str]) -> Decimal:
        return self.pnl if self.attribution in attributions else Decimal(0)

    def set_budget(self, budget: Decimal) -> None:
        self.budget = budget

    def held_contracts(self) -> int:
        return 3

    def waiting_entry(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {}

    async def refresh(self) -> None:
        return None

    async def run(self) -> None:
        return None

    def stop(self) -> None:
        return None

    async def wind_down(self) -> None:
        return None

    async def yield_margin(self, need: Decimal) -> Decimal:
        self.asked.append(need)
        return Decimal(5)


@pytest.fixture(autouse=True)
def clean_running() -> Any:
    leg_engines.RUNNING.clear()
    yield
    leg_engines.RUNNING.clear()


def test_request_margin_asks_running_engines_on_that_market_only() -> None:
    a = FakeEngine("IDX_USDT", "idx@0.1.0")
    b = FakeEngine("IDX_USDT", "idx@0.1.0")
    leg_engines.register_running("GATE", a)
    leg_engines.register_running("BINANCE", b)
    freed = asyncio.run(leg_engines.request_margin("GATE", Decimal(40)))
    assert freed == Decimal(5)
    assert a.asked == [Decimal(40)]  # 목표 가용을 그대로 넘긴다(부족분이 아니다)
    assert b.asked == []


def test_one_engine_per_contract() -> None:
    leg_engines.register_running("GATE", FakeEngine("IDX_USDT", "x@1"))
    with pytest.raises(ValueError, match="이미 엔진"):
        leg_engines.register_running("GATE", FakeEngine("IDX_USDT", "y@1"))


def test_failing_engine_does_not_block_the_entry() -> None:
    class Broken(FakeEngine):
        async def yield_margin(self, need: Decimal) -> Decimal:
            del need
            raise RuntimeError("거래소 조회 실패")

    leg_engines.register_running("GATE", Broken("IDX_USDT", "x@1"))
    assert asyncio.run(leg_engines.request_margin("GATE", Decimal(10))) == Decimal(0)


def test_engine_port_pnl_reaches_fund_but_not_isolated_brake() -> None:
    eng = FakeEngine("IDX_USDT", "idx@0.1.0")
    basket = Basket(as_members([("IDX_USDT", Decimal(1))]), version="v1")
    coord = Coordinator(
        engine=RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)), slots=6),
        ports={"IDX_USDT": eng},  # type: ignore[dict-item]
    )
    coord.isolate(frozenset({"idx@0.1.0"}))
    coord.tick()  # 첫 틱 — mark 를 잡는다
    eng.pnl = Decimal(-100)
    coord.tick()
    assert coord.engine.balance == Decimal(900)  # 펀드 총자본에는 든다
    assert coord.brake_drawdown() == Decimal(0)  # 브레이크 원장에는 안 든다(격리)
    assert eng.budget is not None  # 바구니 구성원이라 예산을 받는다(엔진은 기록만)


def _runner_stub(spare: Decimal, leverage: Decimal) -> Any:
    """`LiveRunner._ask_yield` 가 읽는 것만 가진 러너(생성자 없이)."""
    from types import SimpleNamespace

    from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
    from updown.orchestration.walkforward.live_runner import LiveRunner

    runner = object.__new__(LiveRunner)
    inst = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
    runner._session = SimpleNamespace(instrument=inst, ledger=SimpleNamespace(leverage=leverage))  # type: ignore[attr-defined]
    runner._log = SimpleNamespace(info=lambda *_a, **_k: None)  # type: ignore[attr-defined]

    async def spare_margin() -> Decimal:
        return spare

    runner._spare_margin = spare_margin  # type: ignore[method-assign]
    return runner


def test_live_runner_asks_engines_only_when_spare_is_short() -> None:
    eng = FakeEngine("IDX_USDT", "idx@0.1.0")
    leg_engines.register_running("GATE", eng)
    # 예산 50 · 노출 4 · 배율 6 → 증거금 33.3 · 목표 가용 = max(50, 33.3) = 50
    asyncio.run(_runner_stub(Decimal(30), Decimal(6))._ask_yield(Decimal(50), Decimal(4), "t1"))
    assert eng.asked == [Decimal(50)]
    asyncio.run(_runner_stub(Decimal(80), Decimal(6))._ask_yield(Decimal(50), Decimal(4), "t2"))
    assert eng.asked == [Decimal(50)]  # 가용이 넉넉하면 안 묻는다


def test_live_runner_does_nothing_without_engines() -> None:
    stub = _runner_stub(Decimal(1), Decimal(6))
    asyncio.run(stub._ask_yield(Decimal(50), Decimal(4), "t1"))  # 엔진 없음 — 조용히 지나간다


def test_fund_leg_engine_roundtrip_and_old_saves() -> None:
    leg = FundLeg(
        playbook="idx",
        attribution="idx@0.1.0",
        symbols=("IDX_USDT",),
        leverage=Decimal(3),
        exposure=Decimal(3),
        timeframe="1d",
        slots=6,
        isolated=True,
        engine="some_engine",
    )
    assert FundLeg.from_dict(leg.to_dict()) == leg
    old = leg.to_dict()
    del old["engine"]  # 엔진 다리 앞 저장본
    assert FundLeg.from_dict(old).engine is None


def test_create_fund_runs_engine_legs_as_ports_not_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from updown.apps.api import rebalancer as mod

    spawned: list[tuple[str, str]] = []

    async def fake_spawn(fund_id: str, leg: FundLeg, symbol: str, market: str) -> FakeEngine:
        del market
        spawned.append((fund_id, symbol))
        return FakeEngine(symbol, leg.attribution)

    async def no_session(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("엔진 다리 종목에 세션을 띄우면 안 된다")

    async def fake_tick(fund: Any, flow: Any = None) -> Any:
        del flow
        return fund.coordinator.tick()

    monkeypatch.setattr(mod, "_spawn_engine", fake_spawn)
    monkeypatch.setattr(mod, "_spawn_session", no_session)
    monkeypatch.setattr(mod, "_tick", fake_tick)
    leg = FundLeg(
        playbook="idx",
        attribution="idx@0.1.0",
        symbols=("IDX_USDT",),
        leverage=Decimal(3),
        exposure=Decimal(3),
        timeframe="1d",
        slots=6,
        isolated=True,
        engine="some_engine",
    )
    basket = Basket(as_members([("IDX_USDT", Decimal(1))]), version="v1")
    fund = asyncio.run(
        mod._create_fund(  # pyright: ignore[reportPrivateUsage]
            basket=basket,
            total_cash=Decimal(1000),
            leverage=Decimal(3),
            playbook="idx_bundle",
            label="t",
            weight_mode="slots",
            slots=6,
            legs=[leg],
        )
    )
    try:
        assert spawned == [(fund.fund_id, "IDX_USDT")]  # 펀드 id 를 먼저 정해 엔진에 넘긴다
        assert fund.handles == {}
        assert fund.coordinator.ports["IDX_USDT"] is fund.engines["IDX_USDT"]
        assert "idx@0.1.0" in fund.coordinator.excluded  # 브레이크 계산에서 뺀다
        row = asyncio.run(mod._per_symbol(fund, "IDX_USDT"))  # pyright: ignore[reportPrivateUsage]
        assert "engine" in row and row["holding"] is False
    finally:
        mod.FUNDS.pop(fund.fund_id, None)


def test_engine_row_has_session_row_shape() -> None:
    status = {
        "units": [
            {
                "key": "u1",
                "contracts": 10,
                "fill": "100",
                "stop": "70",
                "ghost": False,
                "opened": "t1",
            },
            {
                "key": "u2",
                "contracts": 10,
                "fill": "98",
                "stop": "68.6",
                "ghost": False,
                "opened": "t2",
            },
            {
                "key": "u3",
                "contracts": 0,
                "fill": "96",
                "stop": None,
                "ghost": True,
                "opened": "t3",
            },
        ],
        "realized": "1.5",
        "margin": "66.0",
        "unrealized": "-2.0",
        "findings": [],
    }
    row = engine_row(status, "1", "지수", "idx")
    assert row["holding"] is True
    assert row["handle"] == ""
    assert row["position"]["entry"] == "99.00"
    assert row["position"]["stop"] == "68.6"
    assert [s["contracts"] for s in row["shares"]] == [10, 10]  # 유령 몫은 안 그린다
    assert row["reconciled"] is True
    flat = engine_row({"units": [], "findings": ["position_mismatch"]}, "1", "지수", "idx")
    assert flat["holding"] is False and "position" not in flat
    assert flat["reconciled"] is False
