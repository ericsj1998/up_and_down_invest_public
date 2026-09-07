"""구조물 순수 로직 검증 (P1-1-2 ~ P1-1-5).

골든 스냅샷은 "출력이 안 바뀌었다"만 지킨다 — 처음부터 틀린 출력을 굳혔으면 영원히
틀린 채로 통과한다. 그래서 **합성 데이터로 규칙 자체**를 따로 검증한다.
"""

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from itertools import pairwise

import pytest

from fixture_loader import load_fixture
from updown.analysis.structures.box import detect_boxes
from updown.analysis.structures.confluence import (
    Confluence,
    PriceZone,
    ZoneKind,
    confluence_at,
    find_confluence,
    zones_at,
)
from updown.analysis.structures.params import (
    BoxParams,
    ChannelParams,
    StructureConfigError,
    StructureParams,
    SwingParams,
    TrendlineParams,
    load_params,
)
from updown.analysis.structures.swing import (
    SwingKind,
    SwingPoint,
    contiguous_segments,
    find_pivots,
    prior_swings,
)
from updown.analysis.structures.tolerance import AtrTolerance, from_candles
from updown.analysis.structures.trendline import (
    TrendlineKind,
    build_channel,
    detect_trendlines,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

_INSTRUMENT = Instrument(
    market=Market.UPBIT,
    symbol="KRW-TEST",
    name="테스트",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def make_candles(
    bars: list[tuple[int, int, int, int]],
    timeframe: Timeframe = Timeframe.H1,
    skip_after: int | None = None,
) -> list[Candle]:
    """`(open, high, low, close)` 목록에서 캔들을 만든다.

    Args:
        bars: 봉 값들.
        timeframe: 시간축.
        skip_after: 이 인덱스 다음에 봉 하나 분량의 구멍을 낸다 (결측 재현용).

    Returns:
        캔들 목록.
    """
    step = timedelta(hours=1) if timeframe is Timeframe.H1 else timedelta(minutes=5)
    candles: list[Candle] = []
    offset = 0
    for index, (open_, high, low, close) in enumerate(bars):
        candles.append(
            Candle(
                instrument=_INSTRUMENT,
                timeframe=timeframe,
                ts=_ORIGIN + step * (index + offset),
                open=Decimal(open_),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=Decimal(1),
            )
        )
        if skip_after is not None and index == skip_after:
            offset += 3
    return candles


def _flat_tolerance(margin: str = "0.1") -> AtrTolerance:
    """어느 봉에서나 같은 절대 오차를 주는 허용 오차.

    Args:
        margin: 절대 오차. 기본 0.1 은 가격 100 기준 10bp 로, ATR 전환 전 고정 %와
            같은 폭이다 — 이 테스트가 검증하는 것은 **연쇄 문제**이지 오차 단위가 아니다.

    Returns:
        배수 1 에 평탄한 ATR 계열을 씌운 허용 오차.
    """
    return AtrTolerance(multiple=Decimal(1), atr=tuple([Decimal(margin)] * 200))


class TestSwing:
    """스윙 포인트 탐지 (spec §6.4)."""

    def test_finds_pivot_high_and_low(self) -> None:
        """가운데가 뾰족하면 스윙이다."""
        candles = make_candles(
            [(10, 11, 9, 10), (10, 12, 9, 10), (10, 20, 8, 10), (10, 12, 9, 10), (10, 11, 9, 10)]
        )
        pivots = find_pivots(candles, Timeframe.H1, SwingParams(2, 2))
        assert [(p.index, p.kind) for p in pivots] == [(2, SwingKind.HIGH), (2, SwingKind.LOW)]

    def test_swing_price_is_the_wick_not_the_body(self) -> None:
        """가격은 `high`/`low` 다 — 꼬리 끝 전역 규칙의 출발점 (spec §6.5)."""
        candles = make_candles(
            [(10, 11, 9, 10), (10, 12, 9, 10), (10, 20, 1, 15), (10, 12, 9, 10), (10, 11, 9, 10)]
        )
        pivots = find_pivots(candles, Timeframe.H1, SwingParams(2, 2))
        by_kind = {pivot.kind: pivot.price for pivot in pivots}
        assert by_kind[SwingKind.HIGH] == Decimal(20), "종가·시가가 아니라 고가여야 한다"
        assert by_kind[SwingKind.LOW] == Decimal(1), "종가·시가가 아니라 저가여야 한다"

    def test_plateau_picks_the_first_bar(self) -> None:
        """같은 고가가 연속이면 **첫 봉**이 스윙이다 (좌엄격/우느슨 — 결정론)."""
        candles = make_candles(
            [
                (10, 11, 9, 10),
                (10, 12, 9, 10),
                (10, 20, 9, 10),
                (10, 20, 9, 10),
                (10, 12, 9, 10),
                (10, 11, 9, 10),
            ]
        )
        pivots = find_pivots(candles, Timeframe.H1, SwingParams(2, 2))
        highs = [p.index for p in pivots if p.kind is SwingKind.HIGH]
        assert highs == [2], f"평평한 고점의 첫 봉만 잡혀야 한다 — 실제: {highs}"

    def test_last_bars_cannot_be_pivots(self) -> None:
        """우측 확인 봉이 없는 마지막 봉들은 판정 불가 — 미래를 당겨쓰지 않는다."""
        rising = [(i, i + 10, i, i + 5) for i in range(10, 30)]
        candles = make_candles(rising)
        pivots = find_pivots(candles, Timeframe.H1, SwingParams(2, 2))
        assert all(p.index <= len(candles) - 3 for p in pivots)

    def test_zigzag_alternates_and_keeps_the_extreme(self) -> None:
        """연속된 같은 종류는 더 극단적인 것만 남는다."""
        candles = make_candles(
            [
                (10, 11, 9, 10),
                (10, 12, 9, 10),
                (10, 20, 9, 10),  # 하이
                (10, 12, 9, 10),
                (10, 13, 9, 10),
                (10, 30, 9, 10),  # 더 높은 하이 — 사이에 로우가 없다
                (10, 12, 9, 10),
                (10, 11, 9, 10),
            ]
        )
        swings = prior_swings(candles, Timeframe.H1, SwingParams(2, 2))
        highs = [(s.index, s.price) for s in swings if s.kind is SwingKind.HIGH]
        assert highs == [(5, Decimal(30))], f"더 높은 하이만 남아야 한다 — 실제: {highs}"

    def test_prior_swings_strictly_alternate_on_real_data(self) -> None:
        """`prior_swings()` 는 실데이터에서도 교대가 깨지지 않는다 (spec §6.4)."""
        fixture = load_fixture("btc_5m_range")
        kinds = [s.kind for s in prior_swings(fixture.candles, fixture.timeframe)]
        assert all(a is not b for a, b in pairwise(kinds))

    def test_find_pivots_keeps_what_zigzag_would_discard(self) -> None:
        """`find_pivots()` 는 교대를 강제하지 않는다 — 추세선의 재료이기 때문이다.

        Note:
            이 차이를 테스트로 못박는 이유: 두 함수를 혼용하면 오르는 저점들이 하나로
            뭉개져 지지선이 사라진다. P1-1 개발 중 실제로 그 상태로 구현됐었다.
        """
        fixture = load_fixture("btc_1h_uptrend")
        pivots = find_pivots(fixture.candles, fixture.timeframe)
        reduced = prior_swings(fixture.candles, fixture.timeframe)
        assert len(pivots) > len(reduced), "zigzag 가 무언가를 버리는 데이터여야 대조가 성립한다"
        assert {(p.index, p.kind) for p in reduced} <= {(p.index, p.kind) for p in pivots}


class TestGapHandling:
    """결측 봉 처리 (P0-8 인계 — 거래소 점검이 연 45회 규모다)."""

    def test_segments_split_at_a_gap(self) -> None:
        """결측을 경계로 구간이 쪼개진다."""
        candles = make_candles([(10, 11, 9, 10)] * 6, skip_after=2)
        assert contiguous_segments(candles, Timeframe.H1) == [(0, 3), (3, 6)]

    def test_no_pivot_straddles_a_gap(self) -> None:
        """구멍을 가로지르는 스윙은 만들지 않는다."""
        # 구멍 직후 봉이 전역 최고가지만, 좌측 비교 봉이 구멍 반대편에 있다.
        candles = make_candles(
            [
                (10, 11, 9, 10),
                (10, 12, 9, 10),
                (10, 13, 9, 10),
                (10, 99, 9, 10),  # 구멍 직후 — 좌측 2봉이 구멍 반대편이다
                (10, 12, 9, 10),
                (10, 11, 9, 10),
            ],
            skip_after=2,
        )
        pivots = find_pivots(candles, Timeframe.H1, SwingParams(2, 2))
        assert 3 not in [p.index for p in pivots], "구멍 반대편 봉과 비교해 스윙을 만들면 안 된다"

    def test_real_gap_fixture_has_two_segments(self) -> None:
        """실제 점검 구간 픽스처가 두 구간으로 나뉜다."""
        fixture = load_fixture("btc_5m_exchange_gap")
        segments = contiguous_segments(fixture.candles, fixture.timeframe)
        assert len(segments) == 2, f"82봉 결측이 경계여야 한다 — 실제 구간: {segments}"

    def test_no_trendline_crosses_the_gap(self) -> None:
        """결측을 가로지르는 추세선은 그리지 않는다.

        Note:
            x축이 봉 번호이므로 82봉 구멍을 한 칸으로 압축한 기울기는 인공물이다.
            이 테스트가 없던 상태에서 실제로 인공 추세선 13개가 만들어졌다.
        """
        fixture = load_fixture("btc_5m_exchange_gap")
        segments = contiguous_segments(fixture.candles, fixture.timeframe)
        boundary = segments[0][1]  # 두 번째 구간의 첫 봉 index
        pivots = find_pivots(fixture.candles, fixture.timeframe)
        lines = detect_trendlines(fixture.candles, pivots)
        assert lines, "이 픽스처에서도 (구간 안쪽) 추세선은 나와야 한다"
        for line in lines:
            first, last = line.touches[0].index, line.touches[-1].index
            assert not (first < boundary <= last), (
                f"결측 경계({boundary})를 가로지르는 {line.kind} 선이 있다: {first}~{last}"
            )

    def test_horizontal_boxes_may_cross_the_gap(self) -> None:
        """수평 레벨에는 같은 제한을 걸지 않는다 — 점검 전후의 같은 가격은 같은 레벨이다."""
        fixture = load_fixture("btc_5m_exchange_gap")
        boundary = contiguous_segments(fixture.candles, fixture.timeframe)[0][1]
        params = StructureParams()
        pivots = find_pivots(fixture.candles, fixture.timeframe)
        tolerance = from_candles(fixture.candles, params.box.cluster_atr_multiple)
        crossing = [
            box
            for box in detect_boxes(pivots, params.box, tolerance)
            if box.touches[0].index < boundary <= box.touches[-1].index
        ]
        assert crossing, (
            "기울기 없는 수평 레벨은 결측을 건너도 유효하다 — 이 픽스처에 실제로 있어야 한다"
        )


class TestScale:
    """규모 회귀 — 픽스처(240~288봉)에서 드러나지 않는 결함을 잡는다."""

    def test_swing_detection_is_linear_not_quadratic(self) -> None:
        """5만봉 스윙 탐지가 초 단위에 끝난다.

        Note:
            `zigzag()` 가 같은 봉 묶음을 찾을 때 남은 리스트를 슬라이스하면 전체가
            O(n²) 이 된다. 실제로 그렇게 구현돼 있었고 **10만봉에서 5.5초**를 먹었다 —
            픽스처 규모에서는 0.01초라 아무 테스트도 잡지 못했다.

            문턱 1.5초는 넉넉하다: O(n) 이면 5만봉에 약 0.05초, O(n²) 면 약 1.3초다.
            타이밍 테스트라 절대값에 의존하지만, 30배 여유가 있어 실행 환경 편차에
            흔들리지 않는다.
        """
        bars = 50_000
        step = timedelta(minutes=5)
        candles = [
            Candle(
                instrument=_INSTRUMENT,
                timeframe=Timeframe.M5,
                ts=_ORIGIN + step * index,
                open=Decimal(100 + index % 7),
                high=Decimal(103 + index % 11),
                low=Decimal(97 - index % 5),
                close=Decimal(100 + index % 7),
                volume=Decimal(1),
            )
            for index in range(bars)
        ]
        started = time.perf_counter()
        swings = prior_swings(candles, Timeframe.M5)
        elapsed = time.perf_counter() - started
        assert swings, "이 데이터에서는 스윙이 나와야 한다"
        assert elapsed < 1.5, (
            f"{bars:,}봉 스윙 탐지에 {elapsed:.2f}초 — O(n²) 로 되돌아갔을 수 있다"
        )


class TestTrendline:
    """추세선 작도 (spec §6.5)."""

    @staticmethod
    def _support_ladder() -> list[Candle]:
        """저가가 정확히 봉당 +1 로 오르는 상승 지지선 재료."""
        bars: list[tuple[int, int, int, int]] = []
        for index in range(40):
            low = 100 + index
            # 6봉 주기로 저가가 선에 닿고, 나머지는 위에 뜬다.
            wick = low if index % 6 == 0 else low + 5
            bars.append((wick + 2, wick + 8, wick, wick + 6))
        return make_candles(bars)

    def test_support_line_anchors_are_lows(self) -> None:
        """지지선 앵커는 전부 `low` 좌표다 (P1-1 DoD 2)."""
        candles = self._support_ladder()
        swings = find_pivots(candles, Timeframe.H1)
        lines = detect_trendlines(candles, swings, TrendlineParams())
        supports = [line for line in lines if line.kind is TrendlineKind.SUPPORT]
        assert supports, "봉당 +1 로 오르는 저가 열에서 지지선이 나와야 한다"
        for line in supports:
            for touch in line.touches:
                candle = next(c for c in candles if c.ts == touch.ts)
                assert touch.price == candle.low
                assert touch.price not in (candle.open, candle.close) or candle.low in (
                    candle.open,
                    candle.close,
                )

    def test_all_anchors_are_wick_coordinates_on_real_data(self) -> None:
        """실데이터 전 픽스처에서 몸통 좌표 사용 0건 (P1-1 DoD 2)."""
        for name in ("btc_1h_uptrend", "btc_1h_downtrend", "btc_5m_range"):
            fixture = load_fixture(name)
            by_ts = {candle.ts: candle for candle in fixture.candles}
            swings = find_pivots(fixture.candles, fixture.timeframe)
            for line in detect_trendlines(fixture.candles, swings, TrendlineParams()):
                wanted = "low" if line.kind is TrendlineKind.SUPPORT else "high"
                for anchor in line.anchors():
                    candle = by_ts[anchor.ts]
                    assert anchor.price == getattr(candle, wanted), (
                        f"{name}: {line.kind} 선의 앵커가 {wanted} 좌표가 아니다"
                    )

    def test_body_break_disqualifies_a_line(self) -> None:
        """구간 안에서 몸통이 선을 넘으면 그 선이 아니다 (spec §6.5)."""
        candles = self._support_ladder()
        swings = find_pivots(candles, Timeframe.H1)
        before = detect_trendlines(candles, swings, TrendlineParams())
        assert before, "기준 상태에서 선이 있어야 대조가 성립한다"

        broken = list(candles)
        middle = 18
        original = broken[middle]
        broken[middle] = Candle(
            instrument=original.instrument,
            timeframe=original.timeframe,
            ts=original.ts,
            open=Decimal(50),  # 몸통이 지지선 훨씬 아래로 내려간다
            high=original.high,
            low=Decimal(49),
            close=Decimal(51),
            volume=original.volume,
        )
        after_swings = find_pivots(broken, Timeframe.H1)
        after = detect_trendlines(broken, after_swings, TrendlineParams())
        spanning = [
            line
            for line in after
            if line.kind is TrendlineKind.SUPPORT
            and line.touches[0].index < middle < line.touches[-1].index
        ]
        assert not spanning, "몸통이 깨뜨린 구간을 지나는 지지선이 남아 있다"

    def test_min_touches_is_enforced(self) -> None:
        """접점 2개짜리 선은 추세선이 아니다."""
        candles = self._support_ladder()
        swings = find_pivots(candles, Timeframe.H1)
        lines = detect_trendlines(candles, swings, TrendlineParams(min_touches=3))
        assert all(line.touch_count >= 3 for line in lines)

    def test_touches_are_separated(self) -> None:
        """접점끼리 최소 간격을 지킨다 — 붙어 있는 두 봉은 한 사건이다."""
        fixture = load_fixture("btc_5m_range")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        params = TrendlineParams()
        for line in detect_trendlines(fixture.candles, swings, params):
            gaps = [b.index - a.index for a, b in zip(line.touches, line.touches[1:], strict=False)]
            assert all(gap >= params.min_anchor_distance_bars for gap in gaps), (
                f"접점 간격 {gaps} 에 {params.min_anchor_distance_bars}봉 미만이 있다"
            )

    def test_touches_never_extrapolate_beyond_anchors(self) -> None:
        """접점은 전부 첫 접점 ~ 마지막 접점 **안**에 있다 (외삽 금지)."""
        fixture = load_fixture("btc_5m_range")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        for line in detect_trendlines(fixture.candles, swings, TrendlineParams()):
            first, last = line.touches[0].index, line.touches[-1].index
            assert all(first <= touch.index <= last for touch in line.touches)

    def test_supports_and_resistances_do_not_mix_swing_kinds(self) -> None:
        """지지선은 로우끼리, 저항선은 하이끼리만 잇는다."""
        fixture = load_fixture("btc_1h_uptrend")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        for line in detect_trendlines(fixture.candles, swings, TrendlineParams()):
            wanted = SwingKind.LOW if line.kind is TrendlineKind.SUPPORT else SwingKind.HIGH
            assert all(touch.kind is wanted for touch in line.touches)

    def test_slope_is_independent_of_ambient_decimal_context(self) -> None:
        """주변 Decimal 컨텍스트가 결과를 바꾸지 않는다 (결정론 — 원칙 P1)."""
        fixture = load_fixture("btc_1h_uptrend")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        baseline = [line.line.slope_per_bar for line in detect_trendlines(fixture.candles, swings)]
        with localcontext() as ctx:
            ctx.prec = 6
            narrow = [
                line.line.slope_per_bar for line in detect_trendlines(fixture.candles, swings)
            ]
        assert baseline == narrow


class TestDedupe:
    """같은 선의 다른 표현 병합 — 판정은 **접점의 기하 소속**이다 (축 J2)."""

    def test_geometric_duplicates_are_merged(self) -> None:
        """접점 인덱스가 부분집합이 아니어도 **선이 같으면** 하나로 남는다.

        Note:
            ⭐ 이것이 P1-6 실측이 드러낸 결함이다. 원래는 접점 인덱스 집합의 포함
            관계만 봤으므로 `{a, b, c}` 와 `{a, b, d}` 는 기하학적으로 같은 선이어도
            둘 다 살아남았다. 400봉 창에 40개 이상 남던 선의 대부분이 이 경우다.

            여기서는 저가가 **정확히** 한 직선 위에 있는 열을 만든다 — 모든 접점 조합이
            같은 선을 만들므로, 술어가 옳으면 살아남는 지지선은 **1개**여야 한다.
        """
        bars: list[tuple[int, int, int, int]] = []
        for index in range(60):
            low = 100 + index  # 봉당 정확히 +1
            wick = low if index % 7 == 0 else low + 4
            bars.append((wick + 2, wick + 8, wick, wick + 6))
        candles = make_candles(bars)
        pivots = find_pivots(candles, Timeframe.H1)
        supports = [
            line
            for line in detect_trendlines(candles, pivots, TrendlineParams())
            if line.kind is TrendlineKind.SUPPORT
        ]
        assert len(supports) == 1, (
            f"같은 직선 위의 접점들이 여러 선으로 남았다 — {len(supports)}개 "
            f"(접점 수: {[line.touch_count for line in supports]})"
        )

    def test_distinct_lines_survive(self) -> None:
        """서로 다른 선은 병합되지 않는다 — 과병합이면 구조물이 사라진다.

        Note:
            J2 의 위험은 반대 방향이다. 술어가 너무 느슨하면 **진짜 다른 선을 하나로**
            묶어 지지·저항을 잃는다.

            **평행하지만 떨어진 두 선**으로 검사한다 — 기하 술어에게 가장 어려운 경우다.
            기울기가 같으므로 기울기만 보는 구현은 통과하지 못하고, 위치를 봐야 갈린다.

            ⚠️ 기울기를 다르게 만드는 시나리오는 처음에 실패했다. 급경사(+3/봉) 구간에서
            딥이 **2봉 전 저가보다 높아** 스윙 자체가 안 생겼기 때문이다 — 그쪽은 코드가
            아니라 시나리오 결함이었다.
        """
        bars: list[tuple[int, int, int, int]] = []
        for index in range(60):
            # 봉당 +1 로 오르는 저가. 후반은 같은 기울기로 30 위로 평행 이동한다.
            low = 100 + index + (0 if index < 30 else 30)
            wick = low if index % 5 == 0 else low + 4
            bars.append((wick + 2, wick + 10, wick, wick + 8))
        candles = make_candles(bars)
        pivots = find_pivots(candles, Timeframe.H1)
        supports = [
            line
            for line in detect_trendlines(candles, pivots, TrendlineParams())
            if line.kind is TrendlineKind.SUPPORT
        ]
        # 같은 기울기, 다른 위치 → 서로 다른 선이다.
        positions = {line.line.price_at(0) for line in supports}
        assert len(positions) >= 2, (
            f"평행하지만 30 떨어진 두 선이 하나로 뭉개졌다 — 선 {len(supports)}개, "
            f"기준 가격 {sorted(positions)}"
        )


class TestChannel:
    """평행 채널 (spec §6.5)."""

    def test_channel_needs_opposite_touches(self) -> None:
        """반대편이 부족하면 채널이 아니라 None 이다."""
        fixture = load_fixture("btc_1h_uptrend")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        lines = detect_trendlines(fixture.candles, swings)
        impossible = ChannelParams(min_opposite_touches=99)
        assert all(
            build_channel(fixture.candles, line, swings, impossible) is None for line in lines
        )

    def test_channel_boundaries_enclose_the_basis(self) -> None:
        """하단 <= 상단이고, 근거 추세선이 두 경계 중 하나다."""
        fixture = load_fixture("btc_5m_downtrend")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        found = 0
        for line in detect_trendlines(fixture.candles, swings):
            channel = build_channel(fixture.candles, line, swings)
            if channel is None:
                continue
            found += 1
            probe = line.touches[0].index
            assert channel.lower.price_at(probe) <= channel.upper.price_at(probe)
            assert channel.lower.slope_per_bar == channel.upper.slope_per_bar, "평행이어야 한다"
        assert found, "이 픽스처에서는 채널이 하나 이상 나와야 한다"


class TestBox:
    """수평 레벨 (박스)."""

    @staticmethod
    def _pivots(prices: list[str], kind: SwingKind) -> list[SwingPoint]:
        """가격만 지정한 스윙들 — 클러스터링 규칙만 떼어내 검증한다."""
        return [
            SwingPoint(
                index=position * 10,
                ts=_ORIGIN + timedelta(hours=position * 10),
                price=Decimal(price),
                kind=kind,
            )
            for position, price in enumerate(prices)
        ]

    def test_cluster_does_not_chain(self) -> None:
        """조금씩 어긋난 점들이 사슬처럼 이어지지 않는다 (single-link 연쇄 방지).

        Note:
            100 / 100.09 / 100.18 — 인접끼리는 10bp 안이지만 양 끝은 18bp 로 벌어진다.
            직전 값과만 비교하면 셋이 한 박스로 묶여 레벨이 실제보다 넓어진다.
        """
        pivots = self._pivots(["100", "100.09", "100.18"], SwingKind.LOW)
        boxes = detect_boxes(pivots, BoxParams(), _flat_tolerance())
        assert all(box.touch_count <= 2 for box in boxes), (
            f"18bp 벌어진 양 끝이 한 박스로 묶였다 — {[b.touch_count for b in boxes]}"
        )

    def test_level_uses_median_not_mean(self) -> None:
        """스파이크 하나가 레벨을 끌고 가지 않는다."""
        pivots = self._pivots(["100", "100", "100", "100.09"], SwingKind.HIGH)
        boxes = detect_boxes(pivots, BoxParams(), _flat_tolerance())
        assert boxes[0].level == Decimal("100"), "중앙값이어야 한다 (평균이면 100.0225)"

    def test_highs_and_lows_are_not_merged(self) -> None:
        """같은 가격대라도 천장과 바닥은 다른 사건이다 (S/R Flip 관측 가능성)."""
        fixture = load_fixture("btc_5m_range")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        for box in detect_boxes(swings, BoxParams(), _flat_tolerance()):
            assert len({touch.kind for touch in box.touches}) == 1


class TestConfluence:
    """합류 점수 — spec §5.5 카테고리 기준."""

    @staticmethod
    def _zone(kind: ZoneKind, low: str, high: str, touches: int = 3) -> PriceZone:
        return PriceZone(kind, Decimal(low), Decimal(high), f"{kind}", touches)

    def test_score_counts_kinds_not_zones(self) -> None:
        """같은 종류 3개가 겹쳐도 1점이다 — §5.5 개수 세기의 함정 방어."""
        zones = [
            self._zone(ZoneKind.TRENDLINE_SUPPORT, "99", "101"),
            self._zone(ZoneKind.TRENDLINE_SUPPORT, "99.5", "100.5"),
            self._zone(ZoneKind.TRENDLINE_SUPPORT, "99.8", "100.2"),
        ]
        result = confluence_at(Decimal(100), zones)
        assert result.score == 1, "상관된 신호 3개는 1표다"
        assert result.zone_count == 3, "개수는 진단용으로 남아야 한다"

    def test_different_kinds_raise_the_score(self) -> None:
        """서로 다른 종류가 겹칠 때만 점수가 오른다."""
        zones = [
            self._zone(ZoneKind.TRENDLINE_SUPPORT, "99", "101"),
            self._zone(ZoneKind.BOX_SUPPORT, "99.5", "100.5"),
            self._zone(ZoneKind.CHANNEL_BOUNDARY, "99.8", "100.2"),
        ]
        assert confluence_at(Decimal(100), zones).score == 3

    def test_no_overlap_scores_zero(self) -> None:
        """근거 없는 자리는 0점이다 — 지어내지 않는다 (spec §4.20)."""
        zones = [self._zone(ZoneKind.BOX_RESISTANCE, "200", "201")]
        assert confluence_at(Decimal(100), zones) == Confluence(Decimal(100), ())

    def test_find_confluence_requires_two_kinds(self) -> None:
        """단일 구조물을 합류로 보고하지 않는다."""
        zones = [
            self._zone(ZoneKind.TRENDLINE_SUPPORT, "99", "101"),
            self._zone(ZoneKind.TRENDLINE_SUPPORT, "99.5", "100.5"),
        ]
        assert find_confluence(zones) == []

    def test_channel_basis_is_not_counted_twice(self) -> None:
        """채널의 근거 추세선은 `TRENDLINE_*` 로 이미 세어졌다."""
        fixture = load_fixture("btc_5m_downtrend")
        swings = find_pivots(fixture.candles, fixture.timeframe)
        lines = detect_trendlines(fixture.candles, swings)
        channels = [
            channel
            for channel in (build_channel(fixture.candles, line, swings) for line in lines)
            if channel is not None
        ]
        assert channels, "이 픽스처에서는 채널이 나와야 한다"
        last = len(fixture.candles) - 1
        zones = zones_at(last, lines, channels, [])
        boundary_zones = [z for z in zones if z.kind is ZoneKind.CHANNEL_BOUNDARY]
        assert len(boundary_zones) == len(channels), (
            "채널마다 파생 경계 1개만 담겨야 한다 (근거선은 추세선 쪽에 있다)"
        )


class TestParams:
    """파라미터 로딩 — 조용한 실패 금지 (절대 규칙 #8)."""

    def test_repo_config_loads(self) -> None:
        """`config/structures.yml` 이 실제로 파싱된다."""
        params = load_params()
        assert params.rule_version.startswith("structures@")
        assert params.swing == SwingParams(2, 2), "표준 이론값이어야 한다 (spec §5.6.1 ②)"

    def test_unknown_key_is_rejected(self) -> None:
        """오타 난 키를 조용히 넘기지 않는다."""
        with pytest.raises(StructureConfigError, match="알 수 없는 키"):
            StructureParams.from_mapping({"swing": {"left_bar": 2}})

    def test_decimal_avoids_float_error(self) -> None:
        """YAML float 를 Decimal 로 정확히 옮긴다."""
        params = StructureParams.from_mapping({"trendline": {"touch_atr_multiple": 0.25}})
        assert params.trendline.touch_atr_multiple == Decimal("0.25")

    def test_min_touches_below_two_is_rejected(self) -> None:
        """직선은 2점으로 정의된다."""
        with pytest.raises(StructureConfigError, match="2 이상"):
            StructureParams.from_mapping({"trendline": {"min_touches": 1}})

    def test_missing_file_falls_back_to_standard_values(self) -> None:
        """파일 부재는 정상 경로다 — 순수 함수 테스트가 설정 없이 돌아야 한다."""
        from pathlib import Path

        assert load_params(Path("config/does_not_exist.yml")) == StructureParams()

    def test_broken_file_is_not_silently_ignored(self, tmp_path: object) -> None:
        """파일이 있는데 깨졌으면 즉시 중단한다."""
        from pathlib import Path

        target = Path(str(tmp_path)) / "structures.yml"
        target.write_text("- not a mapping\n", encoding="utf-8")
        with pytest.raises(StructureConfigError, match="매핑이어야 한다"):
            load_params(target)
