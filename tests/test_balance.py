"""밸런스·임밸런스 구조화 (`structures/balance.py`).

여기서 지키려는 것은 **게이트가 하나**라는 성질이다. 크기·거래량·체류를 게이트로
바꾸는 순간 탐지 개수가 파라미터에 좌우되고, 오더블록에서 겪은 91% 기각이 재현된다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from updown.analysis.structures.balance import (
    Leg,
    LegDirection,
    LegKind,
    broke,
    dominant,
    gap_at,
    label,
    last_balance,
    segment,
    structure,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def bar(index: int, open_: str, close: str, *, wick: str = "0", volume: str = "10") -> Candle:
    """몸통만 지정하는 봉 — 꼬리는 위아래로 `wick` 만큼 붙인다.

    Args:
        index: 봉 번호. open_·close: 시가·종가 (몸통을 정한다).
        wick: 몸통 밖으로 뻗을 꼬리 길이. 몸통 기준 판정을 확인할 때 크게 준다.
        volume: 거래량.

    Returns:
        캔들.
    """
    lo, hi = min(Decimal(open_), Decimal(close)), max(Decimal(open_), Decimal(close))
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=_ORIGIN + timedelta(hours=index),
        open=Decimal(open_),
        high=hi + Decimal(wick),
        low=lo - Decimal(wick),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def rising(start: int, count: int, base: int, step: int) -> list[Candle]:
    """몸통이 계속 위로 뜨는 봉들 — 갭이 연속으로 난다."""
    return [
        bar(start + i, str(base + i * step), str(base + i * step + step - 1)) for i in range(count)
    ]


def ranging(start: int, count: int, low: int, high: int) -> list[Candle]:
    """`low`~`high` 를 왔다갔다 하는 봉들 — 갭이 나지 않는다."""
    return [
        bar(start + i, str(low if i % 2 == 0 else high), str(high if i % 2 == 0 else low))
        for i in range(count)
    ]


class TestGapAt:
    def test_body_gap_up_is_detected(self) -> None:
        """1번 몸통 상단 < 3번 몸통 하단이면 상승 갭이다."""
        candles = [bar(0, "100", "102"), bar(1, "103", "106"), bar(2, "107", "109")]
        assert gap_at(candles, 2) is LegDirection.UP

    def test_body_gap_down_is_symmetric(self) -> None:
        candles = [bar(0, "109", "107"), bar(1, "106", "103"), bar(2, "102", "100")]
        assert gap_at(candles, 2) is LegDirection.DOWN

    def test_touching_is_not_a_gap(self) -> None:
        """딱 맞닿은 것은 갭이 아니라 연속이다 — 등호를 넣지 않는다."""
        candles = [bar(0, "100", "102"), bar(1, "102", "105"), bar(2, "102", "108")]
        assert gap_at(candles, 2) is LegDirection.FLAT

    def test_wicks_do_not_make_a_gap(self) -> None:
        """🔴 **몸통 기준이다** (사용자 지정). 꼬리로 보면 스윕 한 번에 갭이 생긴다."""
        # 꼬리를 크게 붙여 꼬리끼리는 겹치게 두고, 몸통만 떨어뜨린다.
        candles = [
            bar(0, "100", "102", wick="9"),
            bar(1, "103", "106"),
            bar(2, "107", "109", wick="9"),
        ]
        # 몸통은 떨어져 있으므로 갭이다 — 꼬리가 겹쳐도 판정이 안 흔들린다.
        assert gap_at(candles, 2) is LegDirection.UP

        # 반대로 몸통이 겹치면 꼬리가 아무리 벌어져도 갭이 아니다.
        overlap = [
            bar(0, "100", "108", wick="20"),
            bar(1, "101", "107"),
            bar(2, "102", "109", wick="20"),
        ]
        assert gap_at(overlap, 2) is LegDirection.FLAT

    def test_out_of_range_is_flat(self) -> None:
        candles = [bar(0, "100", "102"), bar(1, "103", "106")]
        assert gap_at(candles, 1) is LegDirection.FLAT
        assert gap_at(candles, 99) is LegDirection.FLAT


class TestSegment:
    def test_too_short_gives_nothing(self) -> None:
        assert segment([bar(0, "100", "101"), bar(1, "101", "102")]) == ()

    def test_flat_series_is_one_balance(self) -> None:
        """갭이 없으면 전부 한 밸런스다 — 갇혀서 못 움직였다."""
        legs = segment(ranging(0, 10, 100, 102))
        assert len(legs) == 1
        assert legs[0].kind is LegKind.BALANCE
        assert legs[0].direction is LegDirection.FLAT

    def test_consecutive_gaps_join_into_one_imbalance(self) -> None:
        """🔴 갭 하나마다 끊으면 잔조각이 나온다 — 같은 방향은 이어 붙인다."""
        legs = segment(rising(0, 8, 100, 5))
        impulses = [leg for leg in legs if leg.kind is LegKind.IMBALANCE]
        assert len(impulses) == 1
        assert impulses[0].direction is LegDirection.UP
        assert impulses[0].gaps > 1

    def test_balance_sits_between_two_imbalances(self) -> None:
        candles = [*rising(0, 5, 100, 5), *ranging(5, 8, 130, 133), *rising(13, 5, 200, 5)]
        kinds = [leg.kind for leg in segment(candles)]
        assert kinds.count(LegKind.IMBALANCE) == 2
        assert LegKind.BALANCE in kinds

    def test_legs_never_overlap_and_cover_everything(self) -> None:
        """마디가 겹치거나 비면 구조가 아니다 — 봉 하나가 두 마디에 들어가면 안 된다."""
        candles = [*rising(0, 6, 100, 5), *ranging(6, 7, 140, 143), *rising(13, 6, 200, 5)]
        legs = segment(candles)
        assert legs[0].start == 0
        assert legs[-1].end == len(candles) - 1
        for before, after in pairwise(legs):
            assert after.start == before.end + 1

    def test_trailing_range_becomes_a_balance(self) -> None:
        """🔴 마지막 임밸런스 뒤의 정체는 **진행 중인 밸런스**다 — 버리면 전환 기준을 잃는다."""
        candles = [*rising(0, 5, 100, 5), *ranging(5, 9, 130, 133)]
        legs = segment(candles)
        assert legs[-1].kind is LegKind.BALANCE
        assert legs[-1].end == len(candles) - 1

    def test_bounds_use_bodies_not_wicks(self) -> None:
        """마디 경계가 몸통이다 — 꼬리로 잡으면 스윕 한 번에 구간이 늘어난다."""
        candles = ranging(0, 6, 100, 102)
        stretched = [bar(0, "100", "102", wick="50"), *candles[1:]]
        assert segment(stretched)[0].high == Decimal(102)
        assert segment(stretched)[0].low == Decimal(100)

    def test_opposite_gap_starts_a_new_leg(self) -> None:
        """상승 도중 하락 갭이 나면 끊는다 — 이어 붙이면 '한 방향'이 아니다."""
        candles = [
            *rising(0, 5, 100, 5),
            *[bar(5, "118", "112"), bar(6, "111", "105"), bar(7, "104", "98")],
        ]
        directions = [leg.direction for leg in segment(candles) if leg.kind is LegKind.IMBALANCE]
        assert LegDirection.UP in directions
        assert LegDirection.DOWN in directions

    def test_attributes_are_recorded_but_do_not_gate(self) -> None:
        """🔴 거래량이 0 이어도 탐지는 그대로다 — 속성이지 게이트가 아니다."""
        loud = segment(
            [bar(i, str(100 + i * 5), str(104 + i * 5), volume="9999") for i in range(6)]
        )
        quiet = segment([bar(i, str(100 + i * 5), str(104 + i * 5), volume="0") for i in range(6)])
        assert [leg.kind for leg in loud] == [leg.kind for leg in quiet]
        assert loud[0].volume > quiet[0].volume

    def test_displacement_is_none_without_atr(self) -> None:
        """ATR 을 안 주면 None 이다 — 0 으로 채우면 '안 움직였다'로 읽힌다."""
        legs = segment(rising(0, 5, 100, 5))
        assert legs[0].displacement_atr is None

    def test_displacement_uses_the_starting_atr(self) -> None:
        candles = rising(0, 5, 100, 5)
        atr = [Decimal(10)] * len(candles)
        leg = segment(candles, atr)[0]
        assert leg.displacement_atr is not None
        assert leg.displacement_atr > 0


class TestLastBalance:
    def test_picks_the_most_recent(self) -> None:
        """🔴 최신성이 기준이다 — 과거의 더 큰 밸런스는 시장이 이미 잊었다."""
        candles = [
            *rising(0, 5, 100, 5),
            *ranging(5, 6, 130, 140),  # 넓은 과거 밸런스
            *rising(11, 5, 200, 5),
            *ranging(16, 4, 230, 232),  # 좁은 최신 밸런스
        ]
        found = last_balance(segment(candles), LegDirection.UP)
        assert found is not None
        assert found.end == len(candles) - 1

    def test_none_when_there_is_no_balance(self) -> None:
        assert last_balance(segment(rising(0, 6, 100, 5)), LegDirection.UP) is None


class TestStructure:
    """스윙 골격 위에서 묶기 (`structure`).

    `segment` 가 3봉 갭마다 끊어 잔조각(중앙 3봉)을 냈던 것을 스윙 단위로 키운 것이다.
    게이트는 여전히 몸통 갭 하나이고, 스윙은 **어디서 자를지**만 정한다.
    """

    def test_too_short_gives_nothing(self) -> None:
        assert structure([bar(0, "100", "101")], Timeframe.H1) == ()

    def test_covers_everything_without_overlap(self) -> None:
        """마디가 전 구간을 겹침 없이 덮는다 — 구조의 최소 조건이다."""
        candles = [*rising(0, 8, 100, 5), *ranging(8, 10, 140, 146), *rising(18, 8, 200, 5)]
        legs = structure(candles, Timeframe.H1)
        assert legs[0].start == 0
        assert legs[-1].end == len(candles) - 1
        for before, after in pairwise(legs):
            assert after.start == before.end + 1

    def test_is_coarser_than_raw_gaps(self) -> None:
        """🔴 스윙 골격을 쓰는 **이유**가 이것이다 — 잔조각을 줄인다.

        실측(BTC 300봉): 3봉 갭 93~94 마디 → 스윙 골격 51~74 마디.
        """
        candles = [*rising(0, 10, 100, 4), *ranging(10, 10, 140, 146), *rising(20, 10, 200, 4)]
        assert len(structure(candles, Timeframe.H1)) < len(segment(candles))

    def test_range_without_gaps_is_balance(self) -> None:
        legs = structure(ranging(0, 20, 100, 103), Timeframe.H1)
        assert all(leg.kind is LegKind.BALANCE for leg in legs)

    def test_swingless_series_is_one_balance(self) -> None:
        """스윙이 안 잡히면 통째로 한 마디 — 조각내는 것보다 정직하다."""
        flat = [bar(i, "100", "100") for i in range(10)]
        legs = structure(flat, Timeframe.H1)
        assert len(legs) == 1
        assert legs[0].kind is LegKind.BALANCE

    def test_gate_is_still_only_the_body_gap(self) -> None:
        """🔴 거래량을 바꿔도 마디 구성이 같다 — 스윙이 게이트가 된 것이 아니다."""
        base = [*rising(0, 8, 100, 5), *ranging(8, 8, 140, 146)]
        loud = [bar(i, str(c.open), str(c.close), volume="9999") for i, c in enumerate(base)]
        quiet = [bar(i, str(c.open), str(c.close), volume="1") for i, c in enumerate(base)]
        assert [leg.kind for leg in structure(loud, Timeframe.H1)] == [
            leg.kind for leg in structure(quiet, Timeframe.H1)
        ]


class TestLabel:
    """임밸런스·밸런스는 **주 추세 대비**로 갈린다 (사용자 정의).

    🔴 처음에 이것을 갭 유무로 갈랐다가 틀렸다 — ZigZag 마디는 36~50봉이라 그 안에
    몸통 갭이 반드시 있고, 그래서 1h·1d 에서 밸런스가 0개가 나왔다.
    """

    def test_with_trend_is_imbalance_and_counter_is_balance(self) -> None:
        candles = [
            *rising(0, 8, 100, 5),
            *[bar(8 + i, str(138 - i * 4), str(134 - i * 4)) for i in range(6)],
        ]
        legs = structure(candles, Timeframe.H1)
        up = label(legs, LegDirection.UP)
        for leg in up:
            expected = LegKind.IMBALANCE if leg.direction is LegDirection.UP else LegKind.BALANCE
            assert leg.kind is expected

    def test_flipping_the_trend_flips_every_label(self) -> None:
        """같은 구조에 추세만 뒤집으면 임밸런스와 밸런스가 서로 바뀐다."""
        legs = structure([*rising(0, 8, 100, 5), *ranging(8, 8, 138, 144)], Timeframe.H1)
        up = label(legs, LegDirection.UP)
        down = label(legs, LegDirection.DOWN)
        for a, b in zip(up, down, strict=True):
            assert a.kind is not b.kind or a.direction is LegDirection.FLAT

    def test_flat_trend_makes_everything_balance(self) -> None:
        """방향이 없으면 '추세를 만드는 움직임'도 없다."""
        legs = structure(rising(0, 8, 100, 5), Timeframe.H1)
        assert all(leg.kind is LegKind.BALANCE for leg in label(legs, LegDirection.FLAT))

    def test_label_preserves_bounds_and_attributes(self) -> None:
        """라벨만 바뀌고 좌표·속성은 그대로다 — 라벨링이 구조를 고치면 안 된다."""
        legs = structure([*rising(0, 8, 100, 5), *ranging(8, 8, 138, 144)], Timeframe.H1)
        for before, after in zip(legs, label(legs, LegDirection.UP), strict=True):
            assert (after.start, after.end, after.gaps, after.volume) == (
                before.start,
                before.end,
                before.gaps,
                before.volume,
            )


class TestDominant:
    def test_empty_is_flat(self) -> None:
        assert dominant(()) is LegDirection.FLAT

    def test_picks_the_side_with_more_bars(self) -> None:
        legs = structure([*rising(0, 12, 100, 5), *ranging(12, 4, 158, 161)], Timeframe.H1)
        assert dominant(legs) in {LegDirection.UP, LegDirection.DOWN, LegDirection.FLAT}


class TestBroke:
    """추세 전환 — 직전 밸런스 기준선의 **종가** 이탈 (사용자 매매 룰).

    사용자 규칙: *"상승 → 하락 전환은 첫 상승추세의 밸런스 구간 저점이 깨졌을 때.
    반대로 하락 → 상승은 고점을 뚫어내면서 상승 임밸런스를 가져갈 때."*
    """

    @staticmethod
    def _scene() -> tuple[list[Candle], tuple[Leg, ...]]:
        """상승 임밸런스 → 밸런스(반대 파동) 로 끝나는 장면.

        ⚠️ `ranging` 은 순이동이 0 이라 방향이 UP 으로 잡혀 밸런스가 안 생긴다.
        밸런스는 **추세와 반대 방향** 마디이므로 실제로 내려가는 구간이 필요하다.
        """
        candles = [
            *rising(0, 10, 100, 5),
            *[bar(10 + i, str(145 - i * 3), str(142 - i * 3)) for i in range(6)],
        ]
        legs = label(structure(candles, Timeframe.H1), LegDirection.UP)
        return candles, legs

    def test_none_while_the_level_holds(self) -> None:
        candles, legs = self._scene()
        assert broke(candles, legs, LegDirection.UP) is None

    def test_close_below_the_balance_low_flips_to_down(self) -> None:
        candles, legs = self._scene()
        zone = last_balance(legs, LegDirection.UP)
        assert zone is not None
        # 기준선 아래에서 마감하는 봉을 붙인다.
        broken = [*candles, bar(len(candles), str(zone.low - 1), str(zone.low - 5))]

        found = broke(broken, legs, LegDirection.UP)
        assert found is not None
        assert found.now is LegDirection.DOWN
        assert found.level == zone.low
        assert found.at == len(candles)

    def test_wick_only_is_not_a_break(self) -> None:
        """🔴 꼬리만 넘고 종가가 되돌아온 것은 **유동성 스윕**이지 구조 돌파가 아니다."""
        candles, legs = self._scene()
        zone = last_balance(legs, LegDirection.UP)
        assert zone is not None
        # 저가는 기준선 한참 아래지만 종가는 위에서 마감한다.
        swept = [*candles, bar(len(candles), str(zone.low + 2), str(zone.low + 3), wick="50")]
        assert broke(swept, legs, LegDirection.UP) is None

    def test_downtrend_breaks_upward(self) -> None:
        """하락 중에는 밸런스 **고점** 돌파가 상승 전환이다 — 대칭이다."""
        candles = [
            *[bar(i, str(160 - i * 5), str(156 - i * 5)) for i in range(10)],
            *[bar(10 + i, str(112 + i * 3), str(115 + i * 3)) for i in range(6)],
        ]
        legs = label(structure(candles, Timeframe.H1), LegDirection.DOWN)
        zone = last_balance(legs, LegDirection.DOWN)
        assert zone is not None
        broken = [*candles, bar(len(candles), str(zone.high + 1), str(zone.high + 5))]

        found = broke(broken, legs, LegDirection.DOWN)
        assert found is not None
        assert found.now is LegDirection.UP
        assert found.level == zone.high

    def test_bars_inside_the_balance_do_not_count(self) -> None:
        """⚠️ 구간 안의 등락으로 판정하면 밸런스를 만든 움직임 자체가 이탈이 된다."""
        candles, legs = self._scene()
        zone = last_balance(legs, LegDirection.UP)
        assert zone is not None
        found = broke(candles, legs, LegDirection.UP)
        # 구간 안에 저점을 찍은 봉이 있어도 이탈이 아니다.
        assert found is None

    def test_flat_trend_has_nothing_to_break(self) -> None:
        candles, legs = self._scene()
        assert broke(candles, legs, LegDirection.FLAT) is None


class TestDominantIsPriceBased:
    """🔴 방향은 **가격 이동폭**으로 정한다 (Wilder DMI 발상).

    봉 수로 세면 짧고 급한 하락이 길고 완만한 상승을 이기고, 무엇보다 **동점**이 흔해
    `FLAT` → 전부 밸런스로 칠해지는 상태가 났다 (실측: 5m 배수 2.88).
    """

    def test_short_sharp_move_beats_long_shallow_one(self) -> None:
        """봉 수는 상승이 많지만 **폭**은 하락이 크다 — 하락으로 봐야 한다."""
        candles = [
            # 20봉에 걸쳐 완만히 +40
            *[bar(i, str(100 + i * 2), str(102 + i * 2)) for i in range(20)],
            # 6봉에 급락 -90
            *[bar(20 + i, str(140 - i * 15), str(126 - i * 15)) for i in range(6)],
        ]
        # ⚠️ ATR 을 줘야 ZigZag 골격을 탄다. 없으면 프랙탈로 물러나고, 스윙이 2개
        #    미만이면 통째로 한 마디(FLAT)가 되어 이 축을 못 잰다.
        legs = structure(candles, Timeframe.H1, None, [Decimal(6)] * len(candles))
        assert dominant(legs) is LegDirection.DOWN

    def test_never_paints_everything_balance(self) -> None:
        """🔴 회귀 방지 — `FLAT` 이 나오면 화면 전체가 밸런스가 된다.

        마디가 있는 한 방향을 고른다. 연속값이라 동점이 사실상 안 나지만, 나더라도
        마지막 마디 방향으로 떨어뜨려 FLAT 을 피한다.
        """
        candles = [
            *rising(0, 10, 100, 4),
            *[bar(10 + i, str(136 - i * 4), str(132 - i * 4)) for i in range(10)],
        ]
        legs = structure(candles, Timeframe.H1, None, [Decimal(3)] * len(candles))
        trend = dominant(legs)
        assert trend is not LegDirection.FLAT
        labelled = label(legs, trend)
        assert any(leg.kind is LegKind.IMBALANCE for leg in labelled), (
            "임밸런스가 하나도 없으면 구조가 아니라 빈 그림이다"
        )

    def test_no_legs_is_the_only_flat(self) -> None:
        assert dominant(()) is LegDirection.FLAT
