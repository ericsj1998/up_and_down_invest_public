# pyright: reportPrivateUsage=false
"""시장 전체 값 작업 · 러너 읽개 — 자정 시세 · 빈 날 메우기 · 부분 자료 거부 · 하루 한 번 읽기."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from updown.analysis.indicators.breadth import MarketValues, market_values
from updown.analysis.playbook.types import WashedRule
from updown.marketdata.gate.universe_facts import GateUniverseFacts
from updown.orchestration.rebalancer.breadth_job import (
    SOURCE_CANDLE,
    SOURCE_TICKER,
    BreadthJobConfig,
    BreadthParams,
    run_day,
)
from updown.orchestration.walkforward import live_runner

P = BreadthParams(ma_days=20, lag_days=3, rank_days=30)
RULE = WashedRule(
    ref_days=7, breadth_ma=20, breadth_below=Decimal("0.4"), lag_days=3, lag_rank_days=30, need=2
)
CFG = BreadthJobConfig(pace_s=0.0, min_symbols=30)
NAMES = [f"S{j:03d}_USDT" for j in range(40)]
DAY = date(2026, 11, 18)  # 값의 날 — 자정 = 2026-11-19 00:00 UTC
MIDNIGHT = datetime(2026, 11, 19, tzinfo=UTC)


def price(name: str, day: date) -> Decimal:
    """종목 · 날마다 다른 결정론 종가."""
    j, d = int(name[1:4]), day.toordinal()
    return Decimal(100 + j) + Decimal((d * (j + 3)) % 17) / Decimal(10)


class MemoryStore:
    """`MarketCloseStore` 의 메모리 대역 — 먼저 적힌 값이 남는다."""

    def __init__(self) -> None:
        self.closes: dict[tuple[str, date], tuple[Decimal, str]] = {}
        self.rows: dict[tuple[date, BreadthParams], MarketValues] = {}

    async def days_with_closes(self, market: str, since: date, min_symbols: int) -> set[date]:
        del market
        count: dict[date, int] = {}
        for _name, day in self.closes:
            if day >= since:
                count[day] = count.get(day, 0) + 1
        return {d for d, n in count.items() if n >= min_symbols}

    async def add_closes(
        self, market: str, rows: Sequence[tuple[str, date, Decimal]], source: str
    ) -> None:
        del market
        for name, day, close in rows:
            self.closes.setdefault((name, day), (close, source))

    async def table(self, market: str, days: Sequence[date]) -> dict[str, list[Decimal | None]]:
        del market
        out: dict[str, list[Decimal | None]] = {}
        for (name, day), (close, _src) in self.closes.items():
            if day in days:
                out.setdefault(name, [None] * len(days))[days.index(day)] = close
        return out

    async def breadth(self, market: str, day: date, params: BreadthParams) -> MarketValues | None:
        del market
        return self.rows.get((day, params))

    async def add_breadth(
        self, market: str, day: date, params: BreadthParams, values: MarketValues
    ) -> None:
        del market
        self.rows.setdefault((day, params), values)

    def seed(self, days: Sequence[date]) -> None:
        for d in days:
            for name in NAMES:
                self.closes[(name, d)] = (price(name, d), SOURCE_CANDLE)


class FakeSource:
    """전 종목 종가의 원천 대역 — 부른 횟수를 센다."""

    def __init__(self, *, broken: Sequence[str] = ()) -> None:
        self.calls = {"contracts": 0, "last": 0, "daily": 0}
        self.broken = set(broken)

    async def contracts(self) -> dict[str, tuple[bool, bool]]:
        self.calls["contracts"] += 1
        return dict.fromkeys(NAMES, (True, False)) | {"GONE_USDT": (False, True)}

    async def last_closes(self) -> dict[str, Decimal]:
        self.calls["last"] += 1
        return {name: price(name, DAY) for name in NAMES}

    async def daily_closes(self, contract: str, days: int, *, now: datetime) -> dict[date, Decimal]:
        self.calls["daily"] += 1
        if contract in self.broken:
            raise RuntimeError("조회 실패")
        last = (now - timedelta(days=1)).date() if now >= MIDNIGHT else DAY - timedelta(days=1)
        return {
            last - timedelta(days=k): price(contract, last - timedelta(days=k)) for k in range(days)
        }


def run(store: MemoryStore, source: FakeSource, now: datetime, cfg: BreadthJobConfig = CFG) -> Any:
    return asyncio.run(run_day(store, source, [P], now=now, cfg=cfg))


def days_before(n: int) -> list[date]:
    return [DAY - timedelta(days=k) for k in range(n, 0, -1)]


def test_midnight_capture_is_one_bulk_call_and_writes_the_row() -> None:
    store, source = MemoryStore(), FakeSource()
    store.seed(days_before(P.span - 1))  # 그날만 비어 있다
    out = run(store, source, MIDNIGHT - timedelta(seconds=15))  # 자정 15 초 전
    assert (out.day, out.action, out.captured, out.filled) == (DAY, "computed", 40, 0)
    assert source.calls == {"contracts": 0, "last": 1, "daily": 0}
    assert store.closes[(NAMES[0], DAY)][1] == SOURCE_TICKER
    days = [DAY - timedelta(days=k) for k in range(P.span - 1, -1, -1)]
    table = {n: [price(n, d) for d in days] for n in NAMES}
    want = market_values(table, ma_days=20, lag_days=3, rank_days=30)
    assert store.rows[(DAY, P)] == want and want.breadth is not None and want.lag_ret is not None


def test_existing_row_means_no_exchange_call() -> None:
    store, source = MemoryStore(), FakeSource()
    store.rows[(DAY, P)] = MarketValues(Decimal("0.5"), Decimal("0.01"), 40, 40)
    out = run(store, source, MIDNIGHT + timedelta(hours=5))
    assert out.action == "exists" and sum(source.calls.values()) == 0


def test_first_run_fills_every_symbol_from_daily_bars() -> None:
    store, source = MemoryStore(), FakeSource()
    out = run(store, source, MIDNIGHT + timedelta(hours=2))  # 자정을 놓쳤다 — 시세로 안 쌓는다
    assert (out.action, out.captured, out.filled, out.failed) == ("computed", 0, 40, 0)
    assert source.calls == {"contracts": 1, "last": 0, "daily": 40}  # 폐지 종목은 안 부른다
    assert store.closes[(NAMES[0], DAY)][1] == SOURCE_CANDLE
    assert (DAY, P) in store.rows


def test_a_late_price_is_never_written_as_yesterdays_close() -> None:
    store, source = MemoryStore(), FakeSource()
    store.seed(days_before(P.span - 1))
    run(store, source, MIDNIGHT + timedelta(seconds=CFG.capture_window_s + 1))
    assert source.calls["last"] == 0 and source.calls["daily"] == 40


def test_too_many_failed_symbols_writes_nothing() -> None:
    store, source = MemoryStore(), FakeSource(broken=NAMES[:5])  # 12.5% 실패 > 5%
    out = run(store, source, MIDNIGHT + timedelta(hours=2))
    assert (out.action, out.failed) == ("skipped", 5) and not store.rows and not store.closes


def test_a_few_failed_symbols_are_left_out_but_the_row_is_written() -> None:
    cfg = BreadthJobConfig(pace_s=0.0, min_symbols=30, max_fail_ratio=0.1)
    store, source = MemoryStore(), FakeSource(broken=NAMES[:2])
    out = run(store, source, MIDNIGHT + timedelta(hours=2), cfg)
    assert out.action == "computed" and store.rows[(DAY, P)].breadth_count == 38


def test_no_declaration_means_nothing_to_do() -> None:
    store, source = MemoryStore(), FakeSource()
    out = asyncio.run(run_day(store, source, [], now=MIDNIGHT, cfg=CFG))
    assert out.action == "skipped" and sum(source.calls.values()) == 0


# ── Gate 공개 조회 대역


class FakeClient:
    def __init__(self, body: Any) -> None:
        self.body = body
        self.seen: list[tuple[str, dict[str, str] | None]] = []

    async def get_json(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        self.seen.append((path, params))
        return self.body

    async def aclose(self) -> None:
        return None


def test_last_closes_skips_unreadable_and_non_positive_rows() -> None:
    client = FakeClient(
        [
            {"contract": "A_USDT", "last": "1.25"},
            {"contract": "B_USDT", "last": "0"},
            {"contract": "C_USDT", "last": "x"},
            {"last": "3"},
        ]
    )
    got = asyncio.run(GateUniverseFacts(cast("Any", client)).last_closes())
    assert got == {"A_USDT": Decimal("1.25")}


def test_daily_closes_drops_the_unfinished_bar() -> None:
    t = int(datetime(2026, 11, 17, tzinfo=UTC).timestamp())
    client = FakeClient(
        [
            {"t": t, "c": "10"},
            {"t": t + 86400, "c": "11"},
            {"t": t + 2 * 86400, "c": "12"},  # 2026-11-19 — 아직 안 닫힘
        ]
    )
    now = datetime(2026, 11, 19, 6, tzinfo=UTC)
    got = asyncio.run(GateUniverseFacts(cast("Any", client)).daily_closes("A_USDT", 5, now=now))
    assert got == {date(2026, 11, 17): Decimal(10), date(2026, 11, 18): Decimal(11)}
    assert client.seen[0][1] == {"contract": "A_USDT", "interval": "1d", "limit": "6"}


# ── 러너 읽개 — 하루 한 번 읽고 기억한다


@pytest.fixture
def reader_calls() -> Any:
    calls: list[date] = []
    yield calls
    live_runner.set_market_values_reader(None)


def test_values_are_read_once_per_day_and_shared(reader_calls: list[date]) -> None:
    async def read(_market: str, day: date, _rule: WashedRule) -> Any:
        reader_calls.append(day)
        return (Decimal("0.3"), Decimal("-0.02"))

    live_runner.set_market_values_reader(read)
    now = MIDNIGHT + timedelta(hours=4)

    async def many() -> list[Any]:
        return await asyncio.gather(
            *[live_runner.market_day_values("GATE", DAY, RULE, now=now) for _ in range(20)]
        )

    got = asyncio.run(many())
    assert set(got) == {(Decimal("0.3"), Decimal("-0.02"))} and reader_calls == [DAY]


def test_missing_row_is_unknown_and_only_retried_right_after_midnight(
    reader_calls: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def read(_market: str, day: date, _rule: WashedRule) -> Any:
        reader_calls.append(day)
        return None

    monkeypatch.setattr(live_runner, "MARKET_VALUES_WAIT", 0.0)
    live_runner.set_market_values_reader(read)
    late = asyncio.run(
        live_runner.market_day_values("GATE", DAY, RULE, now=MIDNIGHT + timedelta(hours=4))
    )
    assert late is None and len(reader_calls) == 1
    reader_calls.clear()
    fresh = asyncio.run(
        live_runner.market_day_values("GATE", DAY, RULE, now=MIDNIGHT + timedelta(seconds=5))
    )
    assert fresh is None and len(reader_calls) == live_runner.MARKET_VALUES_TRIES


def test_no_reader_means_unknown() -> None:
    live_runner.set_market_values_reader(None)
    assert asyncio.run(live_runner.market_day_values("GATE", DAY, RULE, now=MIDNIGHT)) is None
