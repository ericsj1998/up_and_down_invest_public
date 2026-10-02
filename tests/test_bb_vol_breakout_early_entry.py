"""T345 — 돌파 롱 30분 조기 진입(P125B)의 **부분 봉 판정** 과 선언 파싱.

규칙: 마지막 봉이 `as_of` 에 아직 안 닫혔으면 부분 봉이다. 부분 봉은 관통 하한을
`early_pen_min_atr` 로 올려 보고(0 이면 셋업을 안 낸다 = 기본), 손절은
min(부분 저가, 종가 - `early_stop_pad_atr` x ATR) 이다. 마감 봉 판정은 한 줄도 안 바뀐다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.detectors.private_strategy import private_strategy
from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import EarlyEntry
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

INSTRUMENT = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ROUND_TRIP = Decimal("0.002")


def bar(i: int, close: Decimal, *, volume: Decimal = Decimal(100)) -> Candle:
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.H1,
        ts=T0 + timedelta(hours=i),
        open=close - Decimal("0.5"),
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=volume,
    )


def window(jump: Decimal, n: int = 200) -> list[Candle]:
    rows = [bar(i, Decimal(100) + Decimal(i) * Decimal("0.05")) for i in range(n - 1)]
    rows.append(bar(n - 1, rows[-1].close + jump, volume=Decimal(300)))
    return rows


def setup_of(rows: list[Candle], **extra: object):
    return private_strategy(
        rows,
        Timeframe.H1,
        ROUND_TRIP,
        bb_period=20,
        bb_k=Decimal(2),
        vol_period=20,
        vol_multiple=Decimal("2.0"),
        sl_atr=Decimal("0"),
        dir_period=20,
        dir_bars=5,
        pen_min_atr=Decimal("0.75"),
        **extra,  # type: ignore[arg-type]
    )


class TestPartialBar:
    def test_closed_bar_is_unchanged_by_the_early_params(self) -> None:
        rows = window(Decimal(6))
        base = setup_of(rows)
        closed_at = rows[-1].ts + timedelta(hours=1)
        same = setup_of(rows, as_of=closed_at, early_pen_min_atr=Decimal("1.25"))
        assert base is not None and same is not None
        assert same.stop_loss == base.stop_loss and same.avg_entry == base.avg_entry

    def test_partial_bar_is_silent_when_disabled(self) -> None:
        rows = window(Decimal(6))
        assert setup_of(rows) is not None
        assert setup_of(rows, as_of=rows[-1].ts + timedelta(minutes=30)) is None

    def test_partial_bar_needs_the_deeper_penetration(self) -> None:
        rows = window(Decimal(6))
        half = rows[-1].ts + timedelta(minutes=30)
        assert setup_of(rows, as_of=half, early_pen_min_atr=Decimal("1.25")) is not None
        assert setup_of(rows, as_of=half, early_pen_min_atr=Decimal("4")) is None

    def test_partial_stop_is_the_lower_of_low_and_padded_close(self) -> None:
        rows = window(Decimal(6))
        half = rows[-1].ts + timedelta(minutes=30)
        got = setup_of(
            rows, as_of=half, early_pen_min_atr=Decimal("1.25"), early_stop_pad_atr=Decimal("5")
        )
        assert got is not None
        assert got.stop_loss < rows[-1].low
        tight = setup_of(
            rows, as_of=half, early_pen_min_atr=Decimal("1.25"), early_stop_pad_atr=Decimal("0")
        )
        assert tight is not None and tight.stop_loss == rows[-1].low


def m15(hh: int, mm: int) -> Candle:
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.M15,
        ts=T0.replace(hour=hh, minute=mm),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )


class TestSessionClock:
    """세션 결함 두 개(2026-10-02 · DOGE 2022-07-20 탐침).

    조기 진입이 한 걸음 일렀고, 진입 전 봉으로 손절됐다.
    """

    def test_now_is_the_end_of_the_last_closed_source_bar(self) -> None:
        from updown.orchestration.walkforward.session import early_clock

        # 07:00 · 07:15 15분봉이 닫혔다 = 지금 07:30 → 1H 봉 시작에서 30분(:30 선언이 걸린다)
        both = [m15(7, 0), m15(7, 15)]
        assert early_clock(both, Timeframe.M15) == T0.replace(hour=7, minute=30)
        # 07:00 하나만 닫혔으면 지금은 07:15 다 — 예전엔 07:30 으로 셈해 15분 부분 봉으로 샀다
        assert early_clock([m15(7, 0)], Timeframe.M15) == T0.replace(hour=7, minute=15)
        assert early_clock([], Timeframe.M15) is None

    def test_stop_confirm_ignores_bars_closed_before_the_entry_bar(self) -> None:
        from updown.orchestration.walkforward.session import confirm_after_entry

        before = bar(6, Decimal(100))  # 06:00 1H — 돌파 앞 봉
        breakout = bar(7, Decimal(100))  # 07:00 1H — 돌파봉
        early_open = T0.replace(hour=7, minute=15)  # 조기 진입(체결 봉 07:15)
        assert not confirm_after_entry(before, early_open, Timeframe.H1)
        assert confirm_after_entry(breakout, early_open, Timeframe.H1)
        # 마감 진입(체결 봉 07:45 · 확인 봉 = 돌파봉 자신)은 예전과 같다
        assert confirm_after_entry(breakout, T0.replace(hour=7, minute=45), Timeframe.H1)
        assert not confirm_after_entry(None, early_open, Timeframe.H1)


class TestDeclaration:
    def test_minute_must_be_inside_the_bar(self) -> None:
        assert EarlyEntry(minute=30).minute == 30
        with pytest.raises(ValueError):
            EarlyEntry(minute=0)
        with pytest.raises(ValueError):
            EarlyEntry(minute=60)

    def test_live_playbooks_do_not_declare_early_entry(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        assert books["private_strategy"].early_entry == EarlyEntry(minute=30)
        assert books["private_strategy"].early_entry is None
        assert all(b.early_entry is None for b in books.values() if b.listed)
