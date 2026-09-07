"""MACD · 볼린저 · 스토캐스틱 — **지금까지 코드에 없던** 셋 (T153).

`snapshot.py` 가 *"macd/bb/vwap 은 항상 None"* 이라고 적어 두고 비워 뒀던 자리다.
E섹션이 1H·4H 에서 MACD 를 ◎ 로 놓았는데 그 칸을 재려면 지표가 있어야 한다.

⭐ pandas-ta 대조는 `test_indicator_parity.py` 와 같은 구조로 붙인다 — 여기서는
**정의 자체**와 함정을 잠근다.
"""

from decimal import Decimal

import pytest

from updown.analysis.indicators.bands import bollinger
from updown.analysis.indicators.ma import ema
from updown.analysis.indicators.macd import macd
from updown.analysis.indicators.series import SeriesError
from updown.analysis.indicators.stochastic import stochastic


def ramp(count: int, step: float = 1.0, base: float = 100.0) -> list[Decimal]:
    return [Decimal(str(base + step * i)) for i in range(count)]


class TestMacd:
    def test_lengths_match_the_input(self) -> None:
        """`series` 계약 1번 — 인덱스가 봉 번호에 그대로 대응해야 한다."""
        got = macd(ramp(200))
        assert len(got.line) == len(got.signal) == len(got.histogram) == 200

    def test_the_signal_is_the_ema_of_the_line_not_of_price(self) -> None:
        """🔴 흔한 구현 실수다. 틀리면 히스토그램의 부호가 다른 시점에 바뀐다."""
        close = ramp(300)
        got = macd(close)
        start = next(index for index, one in enumerate(got.line) if one is not None)
        dense = [one for one in got.line[start:] if one is not None]
        expected = ema(dense, 9)
        for offset, value in enumerate(expected):
            assert got.signal[start + offset] == value

    def test_the_histogram_is_the_difference(self) -> None:
        got = macd(ramp(200))
        for line, signal, hist in zip(got.line, got.signal, got.histogram, strict=True):
            if line is None or signal is None:
                assert hist is None
            else:
                assert hist == line - signal

    def test_a_rising_series_has_a_positive_line(self) -> None:
        got = macd(ramp(300))
        assert got.line[-1] is not None
        assert got.line[-1] > 0

    def test_a_falling_series_has_a_negative_line(self) -> None:
        got = macd(ramp(300, step=-1.0, base=1000.0))
        assert got.line[-1] is not None
        assert got.line[-1] < 0

    def test_warmup_is_none_not_zero(self) -> None:
        """⚠️ 0 으로 채우면 초반에 없는 교차가 생긴다."""
        got = macd(ramp(200))
        assert got.line[0] is None
        assert got.signal[30] is None

    def test_fast_must_be_shorter_than_slow(self) -> None:
        """🔴 뒤집히면 부호가 통째로 바뀌어 모든 신호가 반대가 된다."""
        with pytest.raises(ValueError, match="짧아야"):
            macd(ramp(200), fast=26, slow=12)

    def test_a_zero_period_raises(self) -> None:
        with pytest.raises(SeriesError):
            macd(ramp(200), fast=0)


class TestBollinger:
    def test_a_flat_series_has_zero_width(self) -> None:
        flat = [Decimal(100)] * 50
        got = bollinger(flat)
        assert got.width[-1] == 0
        assert got.upper[-1] == got.lower[-1] == Decimal(100)

    def test_width_is_normalised_by_the_middle(self) -> None:
        """⭐ 그래야 BTC 와 DOGE 를 한 표에 올린다."""
        small = bollinger([Decimal(str(100 + (i % 2))) for i in range(50)])
        large = bollinger([Decimal(str(10000 + (i % 2) * 100)) for i in range(50)])
        assert small.width[-1] is not None
        assert large.width[-1] is not None
        # 같은 상대 변동이면 폭도 같은 크기여야 한다.
        assert small.width[-1] == pytest.approx(large.width[-1], rel=0.01)

    def test_it_uses_population_deviation(self) -> None:
        """⚠️ n-1 로 재면 밴드가 넓어지고 압축 문턱이 조용히 달라진다."""
        close = [Decimal(str(v)) for v in (1, 2, 3, 4, 5)]
        got = bollinger(close, period=5, multiple=Decimal(1))
        # 평균 3, 모집단 분산 2, 편차 sqrt(2) ≈ 1.41421356
        assert got.middle[-1] == Decimal(3)
        assert float(got.upper[-1] or 0) == pytest.approx(3 + 2**0.5, abs=1e-6)

    def test_position_is_percent_b(self) -> None:
        """상단 밖이면 1 초과, 하단 밖이면 0 미만 — 한 값으로 양쪽을 표현한다."""
        rising = ramp(60, step=2.0)
        got = bollinger(rising)
        assert got.position[-1] is not None
        assert got.position[-1] > Decimal("0.5")

    def test_a_frozen_price_has_no_position(self) -> None:
        """⚠️ 밴드가 한 점이면 위치를 못 낸다 — 0.5 로 채우면 없는 중립이 생긴다."""
        flat = [Decimal(100)] * 50
        assert bollinger(flat).position[-1] is None

    def test_warmup_is_none(self) -> None:
        got = bollinger(ramp(50), period=20)
        assert got.middle[18] is None
        assert got.middle[19] is not None

    def test_a_zero_multiple_raises(self) -> None:
        with pytest.raises(ValueError, match="배수"):
            bollinger(ramp(50), multiple=Decimal(0))


class TestStochastic:
    def test_a_close_at_the_high_is_one_hundred(self) -> None:
        high = [Decimal(110)] * 30
        low = [Decimal(90)] * 30
        close = [Decimal(110)] * 30
        got = stochastic(high, low, close)
        assert got.k[-1] == Decimal(100)

    def test_a_close_at_the_low_is_zero(self) -> None:
        high = [Decimal(110)] * 30
        low = [Decimal(90)] * 30
        close = [Decimal(90)] * 30
        got = stochastic(high, low, close)
        assert got.k[-1] == Decimal(0)

    def test_a_frozen_range_is_none(self) -> None:
        """🔴 저유동 구간에 실제로 생긴다 — 50 으로 채우면 그 시간대만 오염된다."""
        same = [Decimal(100)] * 30
        got = stochastic(same, same, same)
        assert got.k[-1] is None

    def test_d_lags_k(self) -> None:
        """%D 는 %K 를 다시 평활한 것 — 워밍업이 더 길다."""
        high = ramp(60, step=1.0, base=110.0)
        low = ramp(60, step=1.0, base=90.0)
        close = ramp(60, step=1.0, base=100.0)
        got = stochastic(high, low, close)
        first_k = next(i for i, one in enumerate(got.k) if one is not None)
        first_d = next(i for i, one in enumerate(got.d) if one is not None)
        assert first_d > first_k

    def test_mismatched_lengths_raise(self) -> None:
        with pytest.raises(ValueError, match="길이가 다르다"):
            stochastic([Decimal(1)], [Decimal(1), Decimal(2)], [Decimal(1)])

    def test_smoothing_does_not_shrink_the_window(self) -> None:
        """⚠️ 결측을 건너뛰고 있는 값만 평균 내면 **더 짧은 창**의 지표가 된다."""
        high = [Decimal(110)] * 30
        low = [Decimal(90)] * 30
        close = [Decimal(100)] * 30
        got = stochastic(high, low, close, period=14, smooth_k=3, smooth_d=3)
        # 14봉 + 3 + 3 = 인덱스 17 부터 %D 가 선다 (0-기반).
        assert got.d[16] is None
        assert got.d[17] is not None
