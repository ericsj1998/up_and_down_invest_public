"""기준(BTC) 4H 봉을 판들이 한 번만 받는다 · 진입 크기 기준을 한 번만 읽는다 (T852 E1 · F5).

지키는 것:

- 나눠 써도 된다고 밝힌 조회 어댑터면, 같은 4H 봉 동안 같은 요청은 한 번만 나간다.
- **방금 닫힌 봉이 아직 안 온 응답은 기억하지 않는다** — 기억하면 다음 경계까지 낡은 값을 돌려준다.
- 더 긴 창을 받아 둔 것이 있어도 부른 쪽이 요청한 창만 돌려준다(값이 달라지지 않게).
- 이름 없는 조회 어댑터(재현 · 대역)는 예전처럼 매번 받는다.
- 진입의 크기 기준은 판정 때 원장에 찍힌 값(`margin_used`)이다 — 주문을 기다리는 사이 예산이
  바뀌어도 계약 수와 노출 분모가 같은 값에서 나온다.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from test_share_runner import BTC, share, two_shares
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.live_runner import REF_SPAN, ref_rows

NOW = datetime(2026, 11, 17, 4, 0, 30, tzinfo=UTC)  # 04:00 경계 30 초 뒤
EDGE = datetime(2026, 11, 17, 4, 0, tzinfo=UTC)


def bar(ts: datetime) -> Candle:
    one = Decimal(1)
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H4,
        ts=ts,
        open=one,
        high=one,
        low=one,
        close=one,
        volume=one,
    )


class Quotes:
    """봉 조회를 세는 가짜 조회 어댑터 — `through` 까지 닫힌 봉을 낸다."""

    def __init__(self, *, key: str | None, through: datetime) -> None:
        if key is not None:
            self.quote_share_key = key
        self.through = through
        self.calls = 0

    async def get_candles(
        self, ref: Any, frame: Any, start: datetime, end: datetime
    ) -> list[Candle]:
        del ref, frame, end
        self.calls += 1
        out: list[Candle] = []
        at = self.through
        while at >= start:
            out.append(bar(at))
            at -= REF_SPAN
        return sorted(out, key=lambda item: item.ts)


@pytest.fixture(autouse=True)
def clean() -> None:
    live_runner._REF_ROWS.clear()  # pyright: ignore[reportPrivateUsage]
    live_runner._REF_FLIGHTS.clear()  # pyright: ignore[reportPrivateUsage]


def get(quotes: Quotes, bars: int, now: datetime = NOW) -> list[Candle]:
    return list(asyncio.run(ref_rows(quotes, BTC, bars, now)))


def test_boards_share_one_request_within_the_same_bar() -> None:
    quotes = Quotes(key="gate", through=EDGE - REF_SPAN)
    first = get(quotes, 100)
    for seconds in (1, 20, 3000):
        assert get(quotes, 100, NOW + timedelta(seconds=seconds)) == first
    assert quotes.calls == 1, "판 58 개가 같은 요청을 58 번 보내지 않는다"


def test_a_response_missing_the_just_closed_bar_is_not_remembered() -> None:
    """경계 직후 거래소가 그 봉을 아직 안 냈다 — 기억하면 4 시간 동안 낡은 값을 돌려준다."""
    quotes = Quotes(key="gate", through=EDGE - 2 * REF_SPAN)
    get(quotes, 100)
    quotes.through = EDGE - REF_SPAN  # 이제 냈다
    rows = get(quotes, 100, NOW + timedelta(seconds=5))
    assert quotes.calls == 2 and rows[-1].ts == EDGE - REF_SPAN


def test_a_new_bar_is_fetched_again() -> None:
    quotes = Quotes(key="gate", through=EDGE - REF_SPAN)
    get(quotes, 100)
    quotes.through = EDGE
    rows = get(quotes, 100, NOW + REF_SPAN)
    assert quotes.calls == 2 and rows[-1].ts == EDGE


def test_a_shorter_request_gets_only_its_own_window() -> None:
    quotes = Quotes(key="gate", through=EDGE - REF_SPAN)
    long = get(quotes, 300)
    short = get(quotes, 100)
    assert quotes.calls == 1
    assert len(short) < len(long) and short == [c for c in long if c.ts >= NOW - REF_SPAN * 100]


def test_a_longer_request_fetches_and_replaces() -> None:
    quotes = Quotes(key="gate", through=EDGE - REF_SPAN)
    get(quotes, 100)
    assert len(get(quotes, 300)) > 100 and quotes.calls == 2
    get(quotes, 100)
    assert quotes.calls == 2, "더 긴 것을 받아 뒀으면 짧은 요청은 다시 안 나간다"


def test_an_adapter_that_did_not_opt_in_is_asked_every_time() -> None:
    quotes = Quotes(key=None, through=EDGE - REF_SPAN)
    get(quotes, 100)
    get(quotes, 100)
    assert quotes.calls == 2


class Slow(Quotes):
    """응답이 늦게 오는 조회 — `fail` 이면 늦게 실패한다(시한 초과 꼴)."""

    def __init__(self, *, fail: bool) -> None:
        super().__init__(key="gate", through=EDGE - REF_SPAN)
        self.fail = fail

    async def get_candles(
        self, ref: Any, frame: Any, start: datetime, end: datetime
    ) -> list[Candle]:
        await asyncio.sleep(0.01)
        rows = await super().get_candles(ref, frame, start, end)
        if self.fail:
            raise TimeoutError("봉 조회 시한 초과")
        return rows


def test_boards_asking_at_the_same_moment_wait_for_one_request() -> None:
    quotes = Slow(fail=False)

    async def together() -> list[Any]:
        return await asyncio.gather(*(ref_rows(quotes, BTC, 100, NOW) for _ in range(58)))

    got = asyncio.run(together())
    assert quotes.calls == 1 and all(list(rows) == list(got[0]) for rows in got)


def test_a_slow_failure_reaches_every_waiting_board_at_once() -> None:
    """🔴 한 줄로 세우면 판마다 차례로 같은 실패를 처음부터 겪는다 — 맨 뒤 판은 판 수 x 시한만큼
    판정을 못 한다. 같은 조회를 기다리면 다 같이 한 번에 실패하고 "모름" 으로 판정한다."""
    quotes = Slow(fail=True)

    async def together() -> list[Any]:
        return await asyncio.gather(
            *(ref_rows(quotes, BTC, 100, NOW) for _ in range(58)), return_exceptions=True
        )

    got = asyncio.run(together())
    assert quotes.calls == 1, "실패도 한 번만 겪는다"
    assert all(isinstance(item, TimeoutError) for item in got)
    quotes.fail = False
    assert get(quotes, 100), "실패는 기억하지 않는다 — 그 뒤에 온 판은 새로 묻는다"
    assert quotes.calls == 2


def test_a_cancelled_waiter_does_not_cut_the_others_off() -> None:
    quotes = Slow(fail=False)

    async def together() -> list[Candle]:
        first = asyncio.ensure_future(ref_rows(quotes, BTC, 100, NOW))
        second = asyncio.ensure_future(ref_rows(quotes, BTC, 100, NOW))
        await asyncio.sleep(0)
        first.cancel()  # 판 하나가 내려갔다
        return list(await second)

    assert asyncio.run(together()) and quotes.calls == 1


def test_the_budget_of_an_entry_is_the_one_stamped_at_the_decision() -> None:
    run, _exch, a, _b = two_shares()
    ledger = run._session.ledger  # pyright: ignore[reportPrivateUsage]
    stamped = dc_replace(a, margin_used=Decimal("155.397"))
    ledger.replace(stamped)
    run._session.ledger = SimpleNamespace(  # pyright: ignore[reportPrivateUsage]
        records=ledger.records, sizing_base=Decimal("156.245")
    )
    assert run._budget_of(a.trade_id) == Decimal("155.397"), (  # pyright: ignore[reportPrivateUsage]
        "주문을 기다리는 사이 예산이 156.245 로 바뀌어도 판정 때 값으로 잰다"
    )
    plain = share("cccccccc3333", stop="90", contracts=1, playbook="crash@0.1.0")
    assert run._budget_of(plain.trade_id) == Decimal("156.245"), "찍힌 값이 없으면 지금 예산"  # pyright: ignore[reportPrivateUsage]
