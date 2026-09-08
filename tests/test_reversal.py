"""추세 전환 신호 — 연속 캔들 · 장악형 (확정 사항 5·6).

막아야 하는 실패:

1. 🔴 **꼬리로 장악을 판정하는 것** — 변동성 큰 봉이 거의 항상 장악형이 된다
2. 도지를 한쪽으로 세어 연속 판정이 조용히 이어지는 것
3. 봉이 모자랄 때 True 를 내 워밍업이 전부 전환으로 잡히는 것
4. 연속과 장악을 AND 로 묶어 신호가 거의 안 나는 것
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.indicators.reversal import (
    engulfing,
    is_bullish,
    reversal_confirmed,
    streak,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
START = datetime(2026, 8, 16, tzinfo=UTC)


def bar(i: int, o: str, c: str, *, high: str | None = None, low: str | None = None) -> Candle:
    """테스트용 봉 — 꼬리는 몸통 바깥으로 넉넉히 둔다."""
    body_high = max(Decimal(o), Decimal(c))
    body_low = min(Decimal(o), Decimal(c))
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=START + timedelta(hours=i),
        open=Decimal(o),
        high=Decimal(high) if high else body_high + Decimal(5),
        low=Decimal(low) if low else body_low - Decimal(5),
        close=Decimal(c),
        volume=Decimal(100),
    )


class TestBullish:
    def test_doji_is_not_bullish(self) -> None:
        """⚠️ 방향이 없는 것을 한쪽으로 세면 연속 판정이 조용히 이어진다."""
        assert not is_bullish(bar(0, "100", "100"))


class TestStreak:
    def test_three_ticks_after_the_transition_bar(self) -> None:
        """사용자 표현 그대로 — "3개 이어진 캔들 색".

        ⚠️ **규칙이 개정됐다** (2026-08-17) — 전환 첫 봉은 안 센다. 그래서 같은 색
        **네** 봉이 있어야 3틱이 된다. 앞에 반대색 봉도 있어야 전환인 줄 안다.
        """
        rows = [bar(0, "120", "100"), *[bar(i, "100", "110") for i in range(1, 5)]]
        assert streak(rows, bullish=True)

    def test_transition_bar_counts(self) -> None:
        """⭐ **전환 첫 봉도 센다** — `[전환봉] 봉 봉` 이 곧 3연속이다.

        한때 이 봉을 뺐다가 되돌렸다 (근거는 `indicators/reversal.STREAK_BARS`).
        """
        rows = [bar(0, "120", "100"), *[bar(i, "100", "110") for i in range(1, 4)]]
        assert streak(rows, bullish=True)

    def test_broken_by_one(self) -> None:
        rows = [bar(0, "100", "110"), bar(1, "110", "105"), bar(2, "105", "115")]
        assert not streak(rows, bullish=True)

    def test_doji_breaks_the_streak(self) -> None:
        rows = [bar(0, "100", "110"), bar(1, "110", "110"), bar(2, "110", "120")]
        assert not streak(rows, bullish=True)

    def test_not_enough_bars_is_false(self) -> None:
        """⛔ "모른다"를 신호로 세면 워밍업이 전부 전환으로 잡힌다."""
        assert not streak([bar(0, "100", "110")], bullish=True)


class TestEngulfing:
    def test_bullish_engulfing(self) -> None:
        """직전 음봉을 지금 양봉이 몸통으로 감싼다."""
        rows = [bar(0, "110", "100"), bar(1, "99", "112")]
        assert engulfing(rows, bullish=True)

    def test_bearish_engulfing(self) -> None:
        rows = [bar(0, "100", "110"), bar(1, "112", "98")]
        assert engulfing(rows, bullish=False)

    def test_same_direction_is_not_engulfing(self) -> None:
        rows = [bar(0, "100", "110"), bar(1, "99", "112")]
        assert not engulfing(rows, bullish=True)

    def test_body_not_wick(self) -> None:
        """🔴 꼬리로 판정하면 변동성 큰 봉이 거의 항상 장악형이 된다."""
        # 꼬리는 크게 감싸지만 몸통은 못 감싼다
        rows = [bar(0, "110", "100"), bar(1, "104", "108", high="200", low="10")]
        assert not engulfing(rows, bullish=True)

    def test_doji_is_not_engulfing(self) -> None:
        """⛔ 몸통이 0 이면 감싸는 것이 없다."""
        rows = [bar(0, "110", "100"), bar(1, "105", "105")]
        assert not engulfing(rows, bullish=True)

    def test_needs_two_bars(self) -> None:
        assert not engulfing([bar(0, "100", "110")], bullish=True)


class TestConfirmation:
    def test_either_one_confirms(self) -> None:
        """🔴 AND 로 묶으면 신호가 거의 안 난다 — 장악은 한 봉, 3틱은 여러 봉이다."""
        only_streak = [bar(0, "120", "100"), *[bar(i, "100", "110") for i in range(1, 5)]]
        assert reversal_confirmed(only_streak, bullish=True)
        # ⭐ 장악형에는 "전환 첫 봉 무시"를 적용하지 않는다 — 적용하면 정의상 영원히
        #    성립하지 않는다 (사용자 확정: *"장악캔들은 혼자로써 추세 전환으로 친다"*).
        only_engulf = [bar(0, "110", "100"), bar(1, "99", "112")]
        assert reversal_confirmed(only_engulf, bullish=True)

    def test_neither_is_no_signal(self) -> None:
        rows = [bar(0, "100", "110"), bar(1, "110", "105")]
        assert not reversal_confirmed(rows, bullish=True)
