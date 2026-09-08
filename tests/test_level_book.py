"""지지·저항 원장 — 레벨은 살아 있고 역할만 바뀐다 (T06b).

막아야 하는 실패:

1. 🔴 **역할을 저장하는 것** — 가격이 상단을 넘으면 저절로 아래가 되어야 한다
2. 🔴 **태어난 것만으로 레벨이 되는 것** — 존중받은 적 없으면 후보가 아니다
3. 레벨이 생기기 **전** 봉으로 접점을 세는 것 (과거를 강했던 것처럼 만든다)
4. 같은 자리에 레벨이 쌓여 원장이 부푸는 것
5. 상단·하단을 "가장 가까운" 것으로 잡아 내부가 정의상 없어지는 것
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from updown.analysis.structures.level_book import (
    Level,
    build_levels,
    roles_at,
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


def bar(i: int, o: str, h: str, low: str, c: str) -> Candle:
    """테스트용 봉."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=START + timedelta(hours=i),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(100),
    )


def level(low: str, high: str, *, support: int = 0, resist: int = 0, born: int = 0) -> Level:
    """테스트용 레벨."""
    return Level(
        zone=PriceRange(low=Decimal(low), high=Decimal(high)),
        born_at=born,
        valid_from=born,
        support_at=(0,) * support,
        resistance_at=(0,) * resist,
    )


class TestRolesAreDerived:
    """🔴 역할은 저장이 아니라 계산이다."""

    def _book(self) -> list[Level]:
        return [
            level("100", "105", support=3, resist=3, born=0),
            level("200", "205", support=2, resist=2, born=1),
            level("300", "305", support=4, resist=4, born=2),
        ]

    def test_upper_becomes_lower_when_price_crosses(self) -> None:
        """⭐ 가격이 넘으면 **전이 코드 없이** 역할이 바뀐다."""
        book = self._book()
        below = roles_at(book, Decimal(150), at=10)
        assert below.upper is not None
        assert below.upper.zone.low == Decimal(200)  # 위에서 가장 가까운 것
        above = roles_at(book, Decimal(400), at=10)
        assert above.lower is not None
        assert above.lower.zone.low == Decimal(300)  # 같은 레벨이 이제 하단

    def test_nearest_wins_not_strongest(self) -> None:
        """🔴 **가까운 레벨이 이긴다** (규칙 G · 사용자 지적 2026-08-17).

        > *"브레이크아웃 터지고 지지 저항 다 해결됐는데, 대체 왜 여전히 이전에
        > 그어뒀던 박스권에서만 매매 대기를 하는거야?"*

        원장은 레벨을 안 지우므로 세기로만 고르면 오래된 굵은 레벨이 새로 생긴 가까운
        레벨을 언제나 이긴다. **지금 매매할 자리**는 가장 최근 가격 근처다.

        ⚠️ 대가가 있다 — 가까운 것으로 고르면 상단과 하단 사이에 아무것도 없어
        `inner` 가 사실상 사라진다. 그것을 알고 택한다 (아래 테스트가 못 박는다).
        """
        got = roles_at(self._book(), Decimal(150), at=10)
        assert got.upper is not None
        assert got.upper.zone.low == Decimal(200)  # 300 이 더 세지만 200 이 더 가깝다

    def test_inner_disappears_when_nearest_wins(self) -> None:
        """⚠️ **가까운 것을 고른 대가**를 명시한다.

        상단·하단이 각각 가장 가까운 것이면 그 사이에 남는 레벨이 없다. `inner` 는
        보조 근거(+0.1 신뢰도)일 뿐이라 감수하지만, 조용히 사라지면 나중에 "왜 내부가
        안 잡히지" 를 붙들게 된다.
        """
        assert roles_at(self._book(), Decimal(150), at=10).inner is None

    def test_no_inner_when_nothing_between(self) -> None:
        book = [level("100", "105", support=2, resist=2), level("300", "305", support=2, resist=2)]
        assert roles_at(book, Decimal(200), at=10).inner is None


class TestStrengthGate:
    def test_untouched_middle_level_is_not_a_candidate(self) -> None:
        """🔴 태어난 것만으로는 레벨이 아니다 — "냅다 꼬리에 박스 다 긋기" 를 막는다.

        ⚠️ 단 창 안 **최고·최저는 예외**다 (아래). 그래서 여기서는 가운데 것이 빠지는지
        본다 — 최고·최저는 자격을 받는다.
        """
        book = [
            level("50", "55", support=2, resist=2),  # 최저 (검증됨)
            level("100", "105"),  # 가운데 · 접점 0 -> 탈락
            level("300", "305", support=2, resist=2),  # 최고 (검증됨)
        ]
        got = roles_at(book, Decimal(200), at=10)
        assert got.inner is None

    def test_extremes_are_candidates_without_touches(self) -> None:
        """🔴 **창 안 최고·최저는 접점 없어도 후보다** (사용자 확정 ②).

        > "가장 높은 자체를 무시할 수는 없어."

        갓 생긴 최고점은 접점을 쌓을 시간이 없었을 뿐이다. 실측에서 상단 후보가
        171·185번째 봉에 생겨 13~29봉밖에 못 지났고, 그래서 통째로 탈락했다.
        """
        book = [level("100", "105"), level("300", "305")]
        got = roles_at(book, Decimal(200), at=10)
        assert got.upper is not None
        assert got.lower is not None

    def test_touched_beats_extreme(self) -> None:
        """⭐ 자격과 세기는 다르다 — 접점 있는 레벨이 같은 쪽에 있으면 그쪽이 이긴다."""
        book = [
            level("100", "105"),  # 최저 · 접점 0
            level("150", "155", support=3, resist=3),  # 검증됨
            level("300", "305"),  # 최고
        ]
        got = roles_at(book, Decimal(200), at=10)
        assert got.lower is not None
        assert got.lower.zone.low == Decimal(150)

    def test_two_sided_beats_one_sided(self) -> None:
        """지지만 6번보다 지지 3·저항 3 이 진짜 레벨이다."""
        one = level("100", "105", support=6)
        both = level("110", "115", support=3, resist=3)
        assert both.strength > one.strength

    def test_not_usable_before_valid_from(self) -> None:
        book = [
            Level(
                zone=PriceRange(Decimal(100), Decimal(105)),
                born_at=5,
                valid_from=9,
                support_at=(0,) * 2,
                resistance_at=(0,) * 2,
            )
        ]
        assert roles_at(book, Decimal(200), at=8).lower is None
        assert roles_at(book, Decimal(200), at=9).lower is not None


class TestBuild:
    def _wave(self) -> tuple[list[Candle], list[Decimal | None]]:
        """마디가 생기는 파형 — 되돌림이 3xATR(=30)을 넘는다."""
        prices = [*range(200, 100, -10), *range(100, 220, 10), *range(210, 90, -10)]
        rows = [bar(i, str(v), str(v + 3), str(v - 3), str(v)) for i, v in enumerate(prices)]
        return rows, [Decimal(10)] * len(rows)

    def test_levels_are_born_at_turns(self) -> None:
        rows, atr = self._wave()
        assert build_levels(rows, atr)

    def test_flat_series_has_no_levels(self) -> None:
        """⭐ 되돌림이 작으면 마디가 없고, 레벨도 없다 — 창이 아니라 구조가 정한다."""
        rows = [bar(i, "100", "101", "99", "100") for i in range(80)]
        assert build_levels(rows, [Decimal(10)] * len(rows)) == []

    def test_nearby_levels_merge(self) -> None:
        """⛔ 같은 자리에 레벨이 쌓이면 옛 스윙 군집과 같은 과탐지가 된다."""
        rows, atr = self._wave()
        book = build_levels(rows, atr)
        mids = sorted(item.mid for item in book)
        for before, after in pairwise(mids):
            assert after - before > Decimal(10) * Decimal("0.5")

    def test_length_mismatch_raises(self) -> None:
        rows, _ = self._wave()
        with pytest.raises(ValueError):
            build_levels(rows, [Decimal(10)] * 5)

    def test_no_window_parameter(self) -> None:
        """🔴 창 인자가 없다 — 그것이 이 모듈의 요점이다."""
        import inspect

        names = set(inspect.signature(build_levels).parameters)
        assert names == {"candles", "atr"}
