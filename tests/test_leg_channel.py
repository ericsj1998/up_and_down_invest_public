"""마디 채널 (`structures/leg_channel.py`) — 축 J6.

여기서 지키려는 성질은 **결정성**이다.

    마디가 정해지면 채널은 하나로 결정된다 → 과탐지가 구조적으로 불가능하다.

기존 추세선은 쌍을 열거하고 임계값으로 거르므로 "몇 개 나올까"를 물어야 했다. 여기서는
그 질문 자체가 성립하지 않는다 — **마디 수만큼** 나온다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.structures.balance import Leg, LegDirection, LegKind
from updown.analysis.structures.leg_channel import (
    MIN_BARS,
    LegChannel,
    channel_of,
    channels,
    whole,
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
_ORIGIN = datetime(2025, 1, 1, tzinfo=UTC)


def bar(index: int, low: int, high: int, *, wick: int = 0) -> Candle:
    """`low`~`high` 를 **몸통**으로 갖는 봉. `wick` 만큼 꼬리를 더 붙인다.

    몸통과 꼬리를 갈라 두는 이유는 채널이 **몸통만** 봐야 하기 때문이다.
    """
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=_ORIGIN + timedelta(hours=index),
        open=Decimal(low),
        high=Decimal(high + wick),
        low=Decimal(low - wick),
        close=Decimal(high),
        volume=Decimal(10),
    )


def leg(start: int, end: int, direction: LegDirection = LegDirection.UP) -> Leg:
    """마디 하나 — 채널은 `start`·`end` 만 본다."""
    return Leg(
        kind=LegKind.IMBALANCE,
        direction=direction,
        start=start,
        end=end,
        low=Decimal(0),
        high=Decimal(0),
        volume=Decimal(0),
        gaps=0,
        displacement_atr=None,
    )


def _never() -> LegChannel:
    """None 이면 여기서 죽는다 — `assert x is not None` 을 줄마다 쓰지 않기 위한 것."""
    raise AssertionError("채널이 None 이다")


def rising(count: int) -> list[Candle]:
    """봉당 10씩 오르는 깔끔한 상승 — 기울기가 정확히 10 이어야 한다."""
    return [bar(i, 100 + i * 10, 110 + i * 10) for i in range(count)]


class TestChannelOf:
    def test_slope_matches_a_clean_trend(self) -> None:
        """봉당 10 오르는 구간이면 기울기도 10 이다."""
        got = channel_of(rising(20), leg(0, 19))
        assert got is not None
        assert abs(got.slope - Decimal(10)) < Decimal("0.001")

    def test_upper_touches_the_highest_body(self) -> None:
        """🔴 상단은 **몸통 끝**에 닿는다 (사용자 룰 · 마디 정의와 같은 기준)."""
        candles = rising(20)
        candles[10] = bar(10, 200, 400)  # 몸통이 크게 튄 봉
        got = channel_of(candles, leg(0, 19))
        assert got is not None
        above = [
            candle
            for i, candle in enumerate(candles)
            if max(candle.open, candle.close)
            > got.upper_start
            + (got.upper_end - got.upper_start) * Decimal(i) / Decimal(19)
            + Decimal("0.001")
        ]
        assert above == []

    def test_lower_contains_every_body(self) -> None:
        candles = rising(20)
        candles[7] = bar(7, 10, 180)  # 몸통이 아래로 크게 튄 봉
        got = channel_of(candles, leg(0, 19))
        assert got is not None
        below = [
            candle
            for i, candle in enumerate(candles)
            if min(candle.open, candle.close)
            < got.lower_start
            + (got.lower_end - got.lower_start) * Decimal(i) / Decimal(19)
            - Decimal("0.001")
        ]
        assert below == []

    def test_upper_and_lower_are_parallel(self) -> None:
        """평행이 아니면 채널이 아니라 쐐기다 — 별개 개념이라 섞지 않는다."""
        got = channel_of(rising(20), leg(0, 19))
        assert got is not None
        assert (got.upper_end - got.upper_start) == (got.lower_end - got.lower_start)

    def test_short_leg_is_skipped_not_forced(self) -> None:
        """3봉에 회귀선을 맞추면 기울기가 노이즈다. 마디는 살리고 채널만 건너뛴다."""
        assert channel_of(rising(10), leg(0, MIN_BARS - 2)) is None

    def test_flat_range_has_zero_slope(self) -> None:
        candles = [bar(i, 100, 110) for i in range(20)]
        got = channel_of(candles, leg(0, 19))
        assert got is not None
        assert got.slope == Decimal(0)

    def test_wicks_do_not_widen_the_channel(self) -> None:
        """🔴 **스윕 꼬리는 채널 밖으로 나간다.** 그것이 의도다.

        유동성 스윕은 구간을 정의하는 사건이 아니라 구간을 **시험하는** 사건이다.
        꼬리를 넣으면 스윕 한 번에 채널이 벌어져 경계가 뜻을 잃는다.
        """
        plain = channel_of(rising(20), leg(0, 19))
        swept = [*rising(20)]
        swept[9] = bar(9, 190, 200, wick=500)  # 몸통은 그대로, 꼬리만 거대하게
        with_wick = channel_of(swept, leg(0, 19))
        assert plain is not None
        assert with_wick is not None
        assert with_wick.width == plain.width

    def test_width_is_the_gap_between_the_two_lines(self) -> None:
        got = channel_of(rising(20), leg(0, 19))
        assert got is not None
        assert got.width == got.upper_start - got.lower_start


class TestChannels:
    def test_one_channel_per_leg(self) -> None:
        """🔴 개수가 **결정된다** — 탐색이 없으므로 과탐지가 불가능하다."""
        candles = rising(60)
        legs = [leg(0, 19), leg(20, 39), leg(40, 59)]
        assert len(channels(candles, legs)) == 3

    def test_no_legs_means_no_channels(self) -> None:
        assert channels(rising(20), []) == ()

    def test_short_legs_drop_out_but_others_survive(self) -> None:
        candles = rising(40)
        got = channels(candles, [leg(0, 1), leg(10, 39)])
        assert len(got) == 1
        assert got[0].start == 10

    def test_order_follows_the_legs(self) -> None:
        """절대 규칙 #5 — 순서가 흔들리면 같은 입력이 다른 화면을 낸다."""
        candles = rising(60)
        legs = [leg(0, 19), leg(20, 39), leg(40, 59)]
        assert [item.start for item in channels(candles, legs)] == [0, 20, 40]

    def test_whole_window_is_one_channel(self) -> None:
        """🔴 창 전체를 하나로 본다 — 마디 채널이 **국면**이면 이건 **흐름**이다.

        사용자 요구: *"전체 큰 흐름의 추세선을 위 아래로 그려주는 마디 채널도 하나
        있으면 좋을 것 같은데."*
        """
        got = whole(rising(60))
        assert got is not None
        assert (got.start, got.end) == (0, 59)
        assert got.kind == "window"

    def test_whole_direction_comes_from_the_slope(self) -> None:
        """방향을 사람이 안 고른다 — 회귀 기울기의 부호가 정한다."""
        assert (whole(rising(40)) or _never()).direction == "up"
        falling = [bar(i, 400 - i * 10, 410 - i * 10) for i in range(40)]
        assert (whole(falling) or _never()).direction == "down"
        flat = [bar(i, 100, 110) for i in range(40)]
        assert (whole(flat) or _never()).direction == "flat"

    def test_whole_uses_the_same_calculation_as_a_leg(self) -> None:
        """마디용·전체용이 갈라지면 같은 화면에서 두 규칙이 그려진다."""
        candles = rising(40)
        assert (whole(candles) or _never()).width == (
            channel_of(candles, leg(0, 39)) or _never()
        ).width

    def test_too_few_bars_is_none_not_a_flat_line(self) -> None:
        assert whole(rising(MIN_BARS - 1)) is None

    def test_prices_serialise_as_strings(self) -> None:
        got = channels(rising(20), [leg(0, 19)])[0].to_dict()
        assert isinstance(got["upper1"], str)
        assert got["x1"] == 0
