"""T608 — 펀드 재현 엔진 다리: 봉 흘림 · 마감 처리 순서 · 증거금 · 가용 막 · 조정자 포트."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from updown.common.domain.instrument import Market
from updown.common.domain.session import load_calendar
from updown.orchestration.fund_replay.engine_leg import ReplayEngineLeg, engine_closes
from updown.orchestration.fund_replay.harness import assemble


@dataclass
class _Unit:
    fill: Decimal
    contracts: int
    ghost: bool = False


@dataclass
class _Book:
    units: dict[str, _Unit] = field(default_factory=dict[str, _Unit])
    closed: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    fees: Decimal = Decimal(0)


class _Engine:
    def __init__(self, symbol: str = "NAS100_USDT") -> None:
        self._symbol = symbol
        self.book = _Book()
        self.days: list[date] = []
        self.findings: list[str] = []
        self.budget: Decimal | None = None
        self._realized = Decimal(0)

    @property
    def symbol(self) -> str:
        return self._symbol

    async def on_close(self, day: date) -> None:
        self.days.append(day)

    async def yield_margin(self, need: Decimal) -> Decimal:
        _ = need
        return Decimal(0)

    def realized(self) -> Decimal:
        return self._realized

    def realized_of(self, attributions: frozenset[str]) -> Decimal:
        _ = attributions
        return self._realized

    def set_budget(self, budget: Decimal) -> None:
        self.budget = budget

    def held_contracts(self) -> int:
        return 0

    def waiting_entry(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {}


class _Sim:
    def __init__(self) -> None:
        self.seen: list[tuple[Decimal, Decimal, Decimal]] = []

    def observe(self, high: Decimal, low: Decimal, last: Decimal) -> None:
        self.seen.append((high, low, last))

    async def margins(self) -> dict[str, str]:
        return {"total": "1000000", "available": "999999"}


@dataclass(frozen=True)
class _Bar:
    ts: datetime
    high: Decimal
    low: Decimal
    close: Decimal


T0 = datetime(2026, 4, 25, tzinfo=UTC)


def _leg(bars: int, closes: list[tuple[datetime, date]]) -> tuple[ReplayEngineLeg, _Engine, _Sim]:
    hours = [
        _Bar(T0 + timedelta(hours=k), Decimal(101), Decimal(99), Decimal(100)) for k in range(bars)
    ]
    sim = _Sim()
    engine = _Engine()
    leg = ReplayEngineLeg(
        symbol="NAS100_USDT",
        hours=hours,
        closes=closes,
        quanto=Decimal("0.0001"),
        leverage=Decimal(3),
        sim=sim,
        engine=engine,
        now_at=T0,
    )
    return leg, engine, sim


def test_advance_feeds_closed_bars_and_processes_due_days_in_order() -> None:
    closes = [
        (T0 + timedelta(hours=2, seconds=90), date(2026, 4, 25)),
        (T0 + timedelta(hours=30), date(2026, 4, 26)),
    ]
    leg, engine, sim = _leg(48, closes)
    ev = asyncio.run(leg.advance(T0 + timedelta(hours=3)))
    assert len(sim.seen) == 3  # 끝이 03:00 이하인 봉 셋
    assert engine.days == [date(2026, 4, 25)] and ev == [("close_day", "2026-04-25")]
    assert leg.now_at == T0 + timedelta(hours=3)
    asyncio.run(leg.advance(T0 + timedelta(hours=31)))
    assert engine.days == [date(2026, 4, 25), date(2026, 4, 26)] and len(sim.seen) == 31


def test_hour_source_hides_bars_after_the_clock() -> None:
    leg, _e, _s = _leg(10, [])
    leg.now_at = T0 + timedelta(hours=4)
    src: Any = leg.hour_source()
    got = asyncio.run(src.get_candles(None, None, T0, T0 + timedelta(days=1)))
    assert [c.ts for c in got] == [T0 + timedelta(hours=k) for k in range(4)]


def test_held_margin_counts_real_units_only() -> None:
    leg, engine, _s = _leg(1, [])
    engine.book.units = {
        "b1": _Unit(Decimal(30000), 10),  # 명목 30 · 배율 3 → 10
        "g1": _Unit(Decimal(30000), 0, ghost=True),
        "c1": _Unit(Decimal(20000), 15),  # 명목 30 → 10
    }
    assert leg.held_margin() == Decimal(20)


def test_margin_view_replaces_available_with_fund_spare() -> None:
    leg, _e, sim = _leg(1, [])
    view: Any = leg.margin_view(lambda: Decimal("123.45"))
    got = asyncio.run(view.margins())
    assert got["available"] == "123.45" and got["total"] == "1000000"
    assert view.observe == sim.observe  # 나머지는 모의 거래소 그대로


def test_engine_closes_only_days_settled_before_end() -> None:
    cal = load_calendar()
    end = datetime(2026, 4, 28, 20, 0, tzinfo=UTC)  # 4-28 마감(20:00Z) + 90초는 end 뒤
    got = engine_closes(cal, Market.NASDAQ, date(2026, 4, 24), end, timedelta(seconds=90))
    assert [d for _, d in got] == [date(2026, 4, 24), date(2026, 4, 27)]  # 25 · 26 주말


def test_assemble_puts_engine_leg_in_basket_and_ports() -> None:
    leg, engine, _s = _leg(1, [])
    coordinator = assemble([], [], Decimal(1000), 10, engines=[leg])
    assert coordinator.ports["NAS100_USDT"] is engine
    assert [m.symbol for m in coordinator.engine.basket.members] == ["NAS100_USDT"]
    assert engine.budget is not None  # 첫 틱이 예산을 줬다
