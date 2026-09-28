"""일봉 채널 돌파 롱 탐지기 (T319 · T320 P7) — 연구 `t279_channel_zone.py` v2.1 과 같은 식인가.

지키는 것:

- 최소제곱선 · 분위가 연구가 쓴 `np.polyfit` · `np.percentile`(선형 보간)과 같은 값이다
  (numpy 는 시험 대조용).
- 오르는 사인 채널을 윗 영역 위로 뚫고 닫힌 날에 롱 셋업이 하나 난다 ·
  손절 = 돌파봉 저가 - 0.2 x ATR(직전 봉).
- 어느 사건이든 나면 12봉 쉰다 — 다음 날 또 뚫어도 안 낸다.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from updown.analysis.detectors.base import MarketContext, RuleParams
from updown.analysis.detectors.private_strategy import (
    DailyChannelBreakDetector,
    fit_channel,
    least_squares,
    percentile,
)
from updown.analysis.indicators.atr import atr
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

BTC = Instrument(Market.BINANCE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
DAY0 = datetime(2022, 1, 1, tzinfo=UTC)


class TestSameMathAsResearch:
    def test_least_squares_is_polyfit(self) -> None:
        rng = random.Random(7)
        ys = [rng.uniform(-5, 5) + 0.3 * i for i in range(130)]
        slope, icpt = least_squares(ys)
        ref = np.polyfit(np.arange(len(ys), dtype=float), np.array(ys), 1)
        assert math.isclose(slope, float(ref[0]), abs_tol=1e-9)
        assert math.isclose(icpt, float(ref[1]), abs_tol=1e-9)

    def test_percentile_is_numpy_linear(self) -> None:
        rng = random.Random(11)
        values = [rng.uniform(-3, 3) for _ in range(61)]
        for q in (10, 50, 90):
            assert math.isclose(
                percentile(values, q), float(np.percentile(values, q)), abs_tol=1e-12
            )


def closes(n: int, *, jump_at: int | None = None) -> list[float]:
    """오르는 사인 채널(가운데 100 + 0.05t · 진폭 5 · 주기 20일) — `jump_at` 부터 위로 뚫는다."""
    out: list[float] = []
    for t in range(n):
        v = 100 + 0.05 * t + 5 * math.sin(2 * math.pi * t / 20)
        if jump_at is not None and t >= jump_at:
            v += 15 + 2 * (t - jump_at)
        out.append(v)
    return out


def candles(values: list[float]) -> list[Candle]:
    out: list[Candle] = []
    prev = values[0]
    for t, c in enumerate(values):
        o = prev
        out.append(
            Candle(
                instrument=BTC,
                timeframe=Timeframe.D1,
                ts=DAY0 + timedelta(days=t),
                open=Decimal(str(round(o, 6))),
                high=Decimal(str(round(max(o, c) + 0.3, 6))),
                low=Decimal(str(round(min(o, c) - 0.3, 6))),
                close=Decimal(str(round(c, 6))),
                volume=Decimal(10),
            )
        )
        prev = c
    return out


def detector() -> DailyChannelBreakDetector:
    return DailyChannelBreakDetector(
        rule_params=RuleParams(rule_id="private_strategy", version="0.1", values={})
    )


def ctx(window: list[Candle]) -> MarketContext:
    return MarketContext(
        instrument=BTC,
        as_of=window[-1].ts,
        candles={Timeframe.D1: window},
        indicators={},
        structures=(),
        geometry={},
        trend={},
    )


class TestTheChannel:
    def test_the_sine_channel_is_a_channel(self) -> None:
        bars = candles(closes(200))
        top = [float(max(b.open, b.close)) for b in bars[60:190]]
        bot = [float(min(b.open, b.close)) for b in bars[60:190]]
        spans = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        prior = spans[189]
        assert prior is not None
        ch = fit_channel(top, bot, float(prior))
        assert ch is not None and ch.alt >= 5 and ch.inside >= 0.85

    def test_no_breakout_no_setup(self) -> None:
        bars = candles(closes(200))
        assert detector().detect(ctx(bars)) == []


class TestTheBreakout:
    def test_breaking_out_gives_one_long(self) -> None:
        jump = 190
        bars = candles(closes(jump + 1, jump_at=jump))
        found = detector().detect(ctx(bars))
        assert len(found) == 1
        setup = found[0]
        last = bars[-1]
        spans = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        prior = spans[-2]
        assert prior is not None
        assert setup.avg_entry == last.close
        assert setup.stop_loss == last.low - Decimal("0.2") * prior, (
            "돌파봉 저가 - 0.2 x ATR(직전 봉)"
        )
        assert setup.stop_loss < setup.avg_entry

    def test_it_rests_twelve_bars_after_an_event(self) -> None:
        """🔴 어느 사건이든 나면 12봉 쉰다 — 다음 날 또 뚫어도 안 낸다(연구 `rest = t + 12`)."""
        jump = 190
        values = closes(jump + 3, jump_at=jump)
        bars = candles(values)
        det = detector()
        fired = [bool(det.detect(ctx(bars[: k + 1]))) for k in range(jump - 5, jump + 3)]
        assert fired.count(True) == 1, fired
        assert fired[5] is True, "돌파 첫날에 낸다"
