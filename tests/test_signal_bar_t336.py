"""T336 — 크기 기울이기(전고점 · 깊이)는 진입 TF 돌파봉으로 잰다 (2026-10-03).

라이브 체결 봉은 걸음 축 5분봉이라 깊이를 5분 BB · ATR 로 재고(1H 단계와 37% 일치)
전고점에 돌파 시간의 앞 45분 고가를 넣었다(1H 기준 "위" 179건 중 41건만 x1.88).
`signal_bar` 가 같은 시간의 1H 봉을 고른다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward.session import signal_bar

INSTRUMENT = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 9, 30, tzinfo=UTC)


def candle(frame: Timeframe, ts: datetime, close: str = "100") -> Candle:
    c = Decimal(close)
    return Candle(
        instrument=INSTRUMENT,
        timeframe=frame,
        ts=ts,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal(1),
    )


H1 = [
    candle(Timeframe.H1, T0 + timedelta(hours=h), str(100 + h)) for h in range(13)
]  # 00:00 ~ 12:00 1H


def test_live_tick_picks_the_same_hour_breakout_bar() -> None:
    tick = candle(
        Timeframe.M5, T0 + timedelta(hours=12, minutes=50)
    )  # 9/30 12:50 5분봉(실계좌 기록 시각)
    got = signal_bar(H1, tick, Timeframe.H1)
    assert got.timeframe is Timeframe.H1 and got.ts == T0 + timedelta(hours=12)
    assert got.close == Decimal(112)


def test_replay_tick_is_already_the_breakout_bar() -> None:
    assert signal_bar(H1, H1[-1], Timeframe.H1) is H1[-1]


def test_a_stale_entry_bar_falls_back_to_the_tick() -> None:
    """조기 진입(진입 TF 봉이 아직 안 닫힘) — 마지막 닫힌 1H 가 한 봉 넘게 앞이면 체결 봉 그대로."""
    tick = candle(Timeframe.M15, T0 + timedelta(hours=13, minutes=15))
    assert signal_bar(H1, tick, Timeframe.H1) is tick
    assert signal_bar([], tick, Timeframe.H1) is tick
