"""거래량 프로파일 — 표준 Volume Profile (playbooks.md 확정 1).

막아야 하는 실패:

1. 🔴 봉 거래량을 **종가 칸에 몰아넣는 것** — 장대봉에서 특히 틀리고, 우리가 다루는
   것이 바로 그 장대봉이다
2. 빈 프로파일을 조용히 돌려줘 "매물대 없음"과 "못 쟀음"이 같아지는 것
3. 구간 몫을 칸 단위로 반올림해 좁은 구간이 0 이나 한 칸으로 튀는 것
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.indicators.volume_profile import (
    VALUE_AREA_SHARE,
    VolumeProfileError,
    build_profile,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.structure import PriceRange

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
START = datetime(2026, 8, 16, tzinfo=UTC)


def bar(i: int, low: str, high: str, volume: str = "100") -> Candle:
    """테스트용 봉 — 시가·종가는 구간 안에 둔다."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M15,
        ts=START + timedelta(minutes=15 * i),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
        volume=Decimal(volume),
    )


class TestDistribution:
    def test_volume_spreads_across_the_bar_range(self) -> None:
        """🔴 종가 칸에 몰지 않는다 — 장대봉이 넓게 퍼져야 한다."""
        rows = [bar(0, "0", "100", "100")]
        profile = build_profile(rows, bins=10)
        # 한 칸에 몰렸다면 나머지가 전부 0 이다
        assert sum(1 for b in profile.bins if b > 0) == 10
        assert all(b == Decimal(10) for b in profile.bins)

    def test_total_is_preserved(self) -> None:
        """분배해도 총량은 그대로다."""
        rows = [bar(0, "0", "100", "100"), bar(1, "40", "60", "50")]
        profile = build_profile(rows, bins=10)
        assert profile.total == Decimal(150)

    def test_narrow_bar_lands_in_one_bin(self) -> None:
        """폭이 한 칸에 들어가면 나눌 것이 없다."""
        rows = [bar(0, "0", "100", "100"), bar(1, "50", "51", "40")]
        profile = build_profile(rows, bins=10)
        assert profile.bins[5] > profile.bins[0]


class TestPoc:
    def test_poc_finds_the_heaviest_price(self) -> None:
        """POC = 거래량이 가장 많았던 가격대."""
        rows = [bar(0, "0", "100", "100"), bar(1, "50", "60", "900")]
        profile = build_profile(rows, bins=10)
        assert Decimal(50) <= profile.poc <= Decimal(60)

    def test_poc_is_deterministic_on_ties(self) -> None:
        """동률이면 낮은 칸 — 고정해야 결정론이 성립한다 (절대 규칙 #5)."""
        rows = [bar(0, "0", "100", "100")]
        profile = build_profile(rows, bins=10)
        assert profile.poc == Decimal(5)


class TestShare:
    def test_share_counts_partial_overlap(self) -> None:
        """⚠️ 칸 경계에 걸치는 부분은 겹치는 비율만큼 센다."""
        rows = [bar(0, "0", "100", "100")]
        profile = build_profile(rows, bins=10)
        half_bin = profile.share_in(PriceRange(low=Decimal(0), high=Decimal(5)))
        assert half_bin == Decimal("0.05")

    def test_share_of_everything_is_one(self) -> None:
        rows = [bar(0, "0", "100", "100")]
        profile = build_profile(rows, bins=10)
        assert profile.share_in(PriceRange(low=Decimal(0), high=Decimal(100))) == Decimal(1)

    def test_share_outside_is_zero(self) -> None:
        rows = [bar(0, "0", "100", "100")]
        profile = build_profile(rows, bins=10)
        assert profile.share_in(PriceRange(low=Decimal(200), high=Decimal(300))) == Decimal(0)

    def test_heavy_zone_has_bigger_share(self) -> None:
        """🔴 이것이 확정 4 의 "강도" 다."""
        rows = [bar(0, "0", "100", "100"), bar(1, "50", "60", "900")]
        profile = build_profile(rows, bins=10)
        heavy = profile.share_in(PriceRange(low=Decimal(50), high=Decimal(60)))
        light = profile.share_in(PriceRange(low=Decimal(0), high=Decimal(10)))
        assert heavy > light * 5


class TestValueArea:
    def test_value_area_covers_the_standard_share(self) -> None:
        """표준 70% 구간."""
        rows = [bar(0, "0", "100", "100"), bar(1, "40", "60", "400")]
        profile = build_profile(rows, bins=10)
        area = profile.value_area()
        assert profile.share_in(area) >= VALUE_AREA_SHARE

    def test_value_area_contains_poc(self) -> None:
        """POC 에서 출발하므로 반드시 포함한다."""
        rows = [bar(0, "0", "100", "100"), bar(1, "40", "60", "400")]
        profile = build_profile(rows, bins=10)
        area = profile.value_area()
        assert area.low <= profile.poc <= area.high


class TestSilentFailure:
    def test_empty_raises(self) -> None:
        """⛔ 빈 프로파일을 돌려주면 "매물대 없음"과 "못 쟀음"이 같아진다."""
        with pytest.raises(VolumeProfileError):
            build_profile([])

    def test_zero_width_raises(self) -> None:
        with pytest.raises(VolumeProfileError):
            build_profile([bar(0, "100", "100")])

    def test_bad_bins_raises(self) -> None:
        with pytest.raises(ValueError):
            build_profile([bar(0, "0", "100")], bins=0)
