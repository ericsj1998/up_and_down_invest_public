"""고른 플래그를 **그 시점 기준으로** 계산해 그릴 수 있는 모양으로 낸다.

## 이 모듈이 지키는 것

    한 플래그 = 한 레이어. 비면 **왜 비었는지**를 함께 낸다.

빈 레이어를 조용히 빼면 화면에서 "그 플래그는 아무것도 못 찾았다"와 "그 플래그를
계산하지 않았다"가 같아 보인다. 점검기의 존재 이유가 그 둘을 가르는 것이므로, 여기서
뭉개면 도구가 도구를 배신한다 (절대 규칙 #8).

## 미래 절단은 여기서 하지 않는다

호출부가 `AsOfSequence.until` 로 자른 캔들을 넘긴다 (`window.py` 참조). 이 모듈은
받은 캔들이 전부라고 믿는다 — 경계 규칙이 두 곳에 있으면 반드시 갈라지기 때문이다.

## 좌표 규약은 백테스트 차트와 같다

추세선은 `chart.line_dict` 를 그대로 쓴다. 같은 개념을 두 규약으로 내면 화면이 둘을
다르게 그리고, 그러면 "점검기에서는 맞는데 백테스트 차트에서는 틀리다" 같은 유령을
쫓게 된다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from updown.analysis.indicators import snapshot as indicator_snapshot
from updown.analysis.structures.balance import (
    ZIGZAG_ATR_MULTIPLE,
    broke,
    dominant,
    label,
)
from updown.analysis.structures.balance import (
    structure as balance_structure,
)
from updown.analysis.structures.box import Box
from updown.analysis.structures.box_range import box_span
from updown.analysis.structures.bundle import StructureBundle, compute_bundle
from updown.analysis.structures.leg_channel import channels as leg_channels
from updown.analysis.structures.leg_channel import whole as leg_whole
from updown.analysis.structures.level_book import (
    build_levels,
    roles_at,
    smart_resistance,
    smart_support,
)
from updown.analysis.structures.params import StructureParams
from updown.analysis.structures.replay import replay_all
from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.analysis.structures.swing_trendline import (
    BODY,
    swings_from_pivots,
)
from updown.analysis.structures.swing_trendline import (
    detect as swing_trendlines,
)
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.wire import candle_json
from updown.orchestration.backtest.chart import extend_to, line_dict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

MIN_BARS = 30
"""이보다 적으면 작도를 시도하지 않는다.

스윙 하나가 5봉을 먹으므로 30봉이면 스윙이 겨우 몇 개다. 그 위에 그은 추세선은
"추세선"이 아니라 우연이고, 화면에 나오면 사람이 그것을 근거로 이의제기를 한다.
"""


@dataclass(frozen=True, slots=True)
class Layer:
    """플래그 하나의 계산 결과.

    Attributes:
        flag: 플래그 id.
        shapes: 그리기용 도형들. 비어 있을 수 있다.
        note: 비었거나 주의할 것이 있으면 그 이유. 화면이 그대로 보여 준다.
    """

    flag: str
    shapes: tuple["Mapping[str, Any]", ...]
    note: str = ""

    @property
    def count(self) -> int:
        """도형 수. `note` 와 함께 봐야 "0건"의 뜻이 정해진다."""
        return len(self.shapes)


@dataclass(frozen=True, slots=True)
class FrameView:
    """시간축 하나의 화면.

    Attributes:
        timeframe: 시간축.
        candles: 이 시점에 **닫혀 있던** 봉들.
        layers: 플래그별 결과.
        note: 이 시간축 전체에 대한 경고 (봉 부족 등).
        bundle: 이 창의 작도 결과. 🔴 셋업 탐지가 **같은 작도**를 써야 화면의 구조물과
            셋업이 어긋나지 않는다 — 따로 계산하면 그 어긋남이 알고리즘 결함처럼
            보이지만 사실은 점검기 결함이다.
    """

    timeframe: Timeframe
    candles: tuple[Candle, ...]
    layers: tuple[Layer, ...]
    note: str = ""
    bundle: StructureBundle | None = None

    def with_layers(self, extra: "Sequence[Layer]") -> "FrameView":
        """레이어를 덧붙인 사본.

        Args:
            extra: 더할 레이어들 (셋업 등 프레임 밖에서 계산된 것).

        Returns:
            새 화면.
        """
        return FrameView(
            timeframe=self.timeframe,
            candles=self.candles,
            layers=(*self.layers, *extra),
            note=self.note,
            bundle=self.bundle,
        )


def _swing_shapes(swings: "Sequence[SwingPoint]") -> list[dict[str, Any]]:
    """스윙 점들."""
    return [
        {
            "index": point.index,
            "ts": point.ts.isoformat(),
            "price": str(point.price),
            "kind": "high" if point.kind is SwingKind.HIGH else "low",
        }
        for point in swings
    ]


def _box_shapes(boxes: "Sequence[Box]") -> list[dict[str, Any]]:
    """박스들 — 백테스트 차트와 같은 키를 쓴다."""
    return [
        {
            "low": str(box.price_range.low),
            "high": str(box.price_range.high),
            "kind": "resistance" if box.kind is SwingKind.HIGH else "support",
            "touches": len(box.touches),
            "first_ts": box.first_ts.isoformat(),
            "last_ts": box.last_ts.isoformat(),
        }
        for box in boxes
    ]


UPBIT_ROUNDTRIP = Decimal("0.00157")
"""UPBIT 왕복 비용 실측 비율 (`config/costs.yml` §1-0b).

⚠️ 점검기 전용 근사다. 판정 경로는 `common/costs.py` 에서 시장별로 읽는다 — 여기서
쓰는 이유는 화면이 종목마다 비용 표를 끌고 오지 않기 때문이며, 코인만 그린다.
"""


def _range_box_layer(
    flag: str, candles: "Sequence[Candle]", indicators: indicator_snapshot.IndicatorSeries
) -> Layer:
    """박스권 — 상단·하단·내부 (원장 기반).

    Args:
        flag: 플래그 id.
        candles: as-of 로 잘린 봉들.
        indicators: 공용 지표 (ATR 을 쓴다).

    Returns:
        레이어. 레벨이 없으면 이유가 `note` 에 담긴다.

    Note:
        🔴 **셋업과 같은 계산을 쓴다.** 예전에는 화면이 `draft_box`(창 기반), 셋업이
        다른 창을 써서 *"화면엔 그려지는데 진입은 안 한다"* 가 나왔다. 화면과 판정이
        다르면 눈으로 검증할 수가 없다.

        ⚠️ 다만 **국면 게이트는 안 건다.** 점검기는 기하를 보는 곳이고, 하락장이라
        매매하지 않는 자리도 레벨은 그려져야 한다. 그 차이는 `note` 가 말한다.
    """
    if not candles:
        return Layer(flag, (), note="봉이 없다")
    book = build_levels(list(candles), indicators.atr14)
    price = candles[-1].close
    # 🔴 **셋업과 같은 문턱으로 그린다.** 안 맞추면 화면에 실효 폭 0.28% 짜리
    #    박스가 그려지는데 셋업은 그것을 버린다 — 사람이 *"왜 진입을 안 하지"* 를
    #    영원히 못 푼다 (이 저장소에서 네 번째다).
    cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(candles[-1].instrument.market)
    roles = roles_at(
        book, price, at=len(candles) - 1, min_span=box_span(price, cost.round_trip_pct)
    )
    picked = [
        (role, level)
        for role, level in (("상단", roles.upper), ("하단", roles.lower), ("내부", roles.inner))
        if level is not None
    ]
    if not picked:
        alive = sum(1 for item in book if item.strength > 0)
        return Layer(
            flag,
            (),
            note=(
                f"레벨 {len(book)}개 생성 · 접점 있는 것 {alive}개 — 위/아래로 쓸 만한 자리가 없다"
            ),
        )
    shapes = tuple(
        {
            "low": str(level.zone.low),
            "high": str(level.zone.high),
            "kind": "resistance" if role == "상단" else "support",
            "role": role,
            "from_index": level.born_at,
            # 🔴 **as-of 로 센다.** 창 전체 누적을 그리면 화면이 판정보다 많은 접점을
            #    보여 주고, 사람은 "접점 3개인데 왜 안 잡지" 를 붙들게 된다.
            "touches": sum(level.hits_at(roles.at)),
            "support_touches": level.hits_at(roles.at)[0],
            "resistance_touches": level.hits_at(roles.at)[1],
            "first_ts": candles[level.born_at].ts.isoformat(),
            "last_ts": candles[-1].ts.isoformat(),
        }
        for role, level in picked
    )
    # 🔴 **스마트 박스는 둘이다** (사용자 확정 2026-08-17). 저항 아랫변에 매달린 것과
    #    지지 안에 든 것 — 매매는 이 둘 사이를 왕복한다. 하나로 그리면 그 왕복이 안 보인다.
    for role, band in (
        ("상단 스마트", smart_resistance(roles.upper) if roles.upper else None),
        ("하단 스마트", smart_support(roles.lower) if roles.lower else None),
    ):
        if band is None:
            continue
        shapes += (
            {
                "low": str(band.low),
                "high": str(band.high),
                "kind": "smart",
                "role": role,
                "from_index": 0,
                "touches": 0,
                "support_touches": 0,
                "resistance_touches": 0,
                "first_ts": candles[0].ts.isoformat(),
                "last_ts": candles[-1].ts.isoformat(),
            },
        )
    width = ""
    if roles.upper is not None and roles.lower is not None:
        span = (roles.upper.zone.low - roles.lower.zone.high) / price * 100
        width = f" · 실효 폭 {span:.2f}%"
    # 🔴 매매는 **여기서** 낸다. 셋업 도형에 달아 두면 지금 셋업이 없는 화면에서
    #    과거 매매까지 통째로 사라진다 — 레벨은 항상 그려지므로 여기가 제자리다.
    trades = replay_all(list(candles), indicators.atr14)
    shapes += tuple(
        {
            "kind": "trade",
            "number": trade.number,
            "closed": trade.closed,
            "marks": [
                {"event": mark.event.value, "index": mark.index, "price": str(mark.price)}
                for mark in trade.marks
            ],
        }
        for trade in trades
    )
    done = sum(1 for trade in trades if trade.closed)
    count = f" · 매매 {len(trades)}건" if trades else " · 매매 없음"
    if trades and done != len(trades):
        count += f" (보유 중 {len(trades) - done})"
    return Layer(
        flag,
        shapes,
        note=f"원장 {len(book)}개 중 {len(picked)}개 배치{width}{count}",
    )


def _channel_shapes(bundle: StructureBundle, last: int) -> list[dict[str, Any]]:
    """채널들 — 상·하단을 앵커 구간에서만 그린다."""
    shapes: list[dict[str, Any]] = []
    for channel in bundle.channels:
        first = channel.basis.touches[0].index
        anchor = channel.basis.touches[-1].index
        end = extend_to(channel.basis, last)
        shapes.append(
            {
                "basis": channel.basis.kind.value,
                "x1": first,
                "anchor_x2": anchor,
                "x2": end,
                "lower1": str(channel.lower.price_at(first)),
                "lower2": str(channel.lower.price_at(end)),
                "upper1": str(channel.upper.price_at(first)),
                "upper2": str(channel.upper.price_at(end)),
                "opposite_touches": len(channel.opposite_touches),
            }
        )
    return shapes


def _series_shape(name: str, values: "Sequence[Any]") -> list[dict[str, Any]]:
    """지표 시리즈 하나 — 값 배열 그대로 낸다.

    Note:
        `None` 을 0 으로 채우지 않는다. 워밍업 구간의 0 은 "값이 0"으로 그려지고,
        그 계단이 지표 결함으로 보인다.
    """
    return [
        {
            "name": name,
            "values": [None if value is None else str(value) for value in values],
        }
    ]


def _build_layer(
    flag: str,
    candles: "Sequence[Candle]",
    timeframe: Timeframe,
    bundle: StructureBundle,
    indicators: indicator_snapshot.IndicatorSeries,
    deviation: Decimal,
) -> Layer:
    """플래그 하나를 계산한다.

    Args:
        flag: 플래그 id.
        candles: as-of 로 잘린 봉들.
        timeframe: 시간축.
        bundle: 공용 작도 결과.
        indicators: 공용 지표.
        deviation: ZigZag 편차 (ATR 배수).

    Returns:
        레이어. 계산할 수 없으면 `note` 에 이유가 담긴다.
    """
    last = len(candles) - 1
    if flag == "structure.swing":
        return Layer(flag, tuple(_swing_shapes(bundle.swings)))
    if flag == "structure.pivot":
        # 🔴 **추세선·박스가 실제로 보는 점들**이다 (`bundle.pivots` = `find_pivots`).
        #    `bundle.swings` 는 여기서 교대 정리를 거친 부분집합이고, 그동안 화면에는
        #    그쪽만 그려졌다 — 실측(KRW-BTC 1h 200봉)에서 추세선 56개 중 **25개**가
        #    화면에 없는 점을 접점으로 쓰고 있었다. 눈으로 검증하라고 만든 화면이
        #    검증에 필요한 것을 안 그리고 있었다는 뜻이다 (절대 규칙 #8).
        return Layer(flag, tuple(_swing_shapes(bundle.pivots)))
    if flag == "structure.trendline":
        return Layer(flag, tuple(line_dict(line, last) for line in bundle.trendlines))
    if flag == "structure.channel":
        return Layer(flag, tuple(_channel_shapes(bundle, last)))
    if flag == "structure.box":
        return Layer(flag, tuple(_box_shapes(bundle.boxes)))
    if flag == "structure.box_range":
        return _range_box_layer(flag, candles, indicators)
    if flag == "structure.trend_channel":
        # 🔴 마디를 안 본다 — 창 전체가 구간이다. 그래서 밸런스 계산이 필요 없고,
        #    ZigZag 편차를 바꿔도 이 선은 안 움직인다 (`keys_for` 가 그것을 안다).
        found = leg_whole(candles)
        return Layer(
            flag,
            () if found is None else (found.to_dict(),),
            note="" if found is not None else f"봉 {len(candles)}개로는 회귀선을 못 맞춘다",
        )
    if flag in {
        "structure.balance",
        "structure.leg_channel",
        "structure.swing_trendline",
        "trend.structure",
        "trend.break",
    }:
        return _balance_family(flag, candles, timeframe, indicators, deviation, bundle.pivots)
    if flag == "indicator.atr":
        return Layer(flag, tuple(_series_shape("atr14", indicators.atr14)))
    if flag == "indicator.rsi":
        return Layer(flag, tuple(_series_shape("rsi14", indicators.rsi14)))
    if flag == "indicator.volume":
        return Layer(flag, tuple(_series_shape("volume_ratio", indicators.volume_ratio)))
    if flag == "indicator.ma":
        shapes = [
            item
            for period, values in sorted(indicators.ema.items())
            for item in _series_shape(f"ema{period}", values)
        ]
        return Layer(flag, tuple(shapes))
    return Layer(flag, (), note=f"계산이 붙지 않은 플래그다: {flag}")


def _balance_family(
    flag: str,
    candles: "Sequence[Candle]",
    timeframe: Timeframe,
    indicators: indicator_snapshot.IndicatorSeries,
    deviation: Decimal,
    pivots: "Sequence[SwingPoint]" = (),
) -> Layer:
    """밸런스 골격에서 나오는 셋 — 마디 · 주 추세 · 구조 이탈.

    Args:
        flag: 셋 중 하나.
        candles: 봉들.
        timeframe: 시간축.
        pivots: 프랙탈 피벗 (추세선 앵커용). 다른 플래그는 안 쓴다.
        indicators: 지표 (ATR 을 쓴다).
        deviation: ZigZag 편차.

    Returns:
        레이어.

    Note:
        셋을 한 함수에 둔 이유는 **같은 골격에서 나오기** 때문이다. 따로 계산하면
        같은 화면에서 마디와 추세가 어긋날 수 있고, 그 어긋남은 알고리즘 결함처럼
        보이지만 사실은 점검기 결함이다.
    """
    raw = balance_structure(candles, timeframe, None, indicators.atr14, deviation)
    trend = dominant(raw)
    legs = label(raw, trend)
    if flag == "structure.swing_trendline":
        # 🔴 앵커는 **프랙탈 피벗**이다 (ZigZag 전환점이 아니다).
        #
        #    전환점으로 만들었더니 앵커가 너무 적었다 — 실측(200봉 1h · 10개 창):
        #    마디 4~8개 → 전환점 5~9개 → 같은 종류 스윙 2~5개 → 껍질 변 1~3개 →
        #    방향 필터 통과 **0~3개**. 사용자가 "추세선이 가끔 하나만 그려진다"고
        #    한 것이 이 상태다.
        #
        #    피벗은 같은 창에서 50~60개이고, 껍질이 후보를 알아서 줄이므로(껍질 위
        #    점만 변이 된다) 옛 추세선처럼 조합이 폭발하지 않는다.
        # ATR 을 넘겨 공간을 배수로 재게 한다 — 안 넘기면 종목마다 뜻이 달라진다.
        window = [v for v in indicators.atr14 if v is not None]
        mean_atr = sum(window, Decimal(0)) / len(window) if window else None
        # 🔴 꼬리·몸통 두 벌을 넘긴다. 어느 쪽이 나은지는 창마다 다르므로
        #    `detect` 가 **캔들에 더 붙는 쪽**을 고른다 (사용자 지정).
        # 🔴 마디 구간을 넘겨준다 — 마디를 하나도 못 품는 선은 버린다 (`spans_a_leg`).
        #    사용자가 "한 구간 안에 잔뜩 그려놨어"라고 한 그 구간이다.
        lines = swing_trendlines(
            swings_from_pivots(candles, pivots),
            swings_from_pivots(candles, pivots, BODY),
            mean_atr,
            candles,
            legs=[(leg.start, leg.end) for leg in legs],
        )
        return Layer(
            flag,
            tuple(line.to_dict(len(candles) - 1) for line in lines),
            note=(
                ""
                if lines
                else f"피벗 {len(pivots)}개 · 마디 {len(legs)}개 — 마디를 넘는 선이 안 나온다"
            ),
        )
    if flag == "structure.leg_channel":
        # 🔴 탐색이 없다 — 마디가 정해지면 채널은 하나로 결정된다. 그래서 "몇 개
        #    나올까"를 묻지 않아도 되고, 과탐지가 구조적으로 불가능하다.
        found = leg_channels(candles, legs)
        return Layer(
            flag,
            tuple(item.to_dict() for item in found),
            note=(
                ""
                if found
                else f"마디 {len(legs)}개가 전부 너무 짧다 — 회귀선을 맞출 봉이 모자란다"
            ),
        )
    if flag == "trend.structure":
        return Layer(
            flag,
            (
                {
                    "trend": trend.value,
                    "source": "structure",
                    "deviation": str(deviation),
                    "judged_deviation": str(ZIGZAG_ATR_MULTIPLE),
                    "legs": len(legs),
                },
            ),
            note=(
                ""
                if deviation == ZIGZAG_ATR_MULTIPLE
                else f"⚠️ 보기 배수 {deviation}x 로 본 구조다 — 판정 배수는 {ZIGZAG_ATR_MULTIPLE}x"
            ),
        )
    if flag == "trend.break":
        turn = broke(candles, legs, trend)
        if turn is None:
            return Layer(flag, (), note="이탈 없음 — 지금 추세의 전제가 아직 살아 있다")
        return Layer(
            flag,
            (
                {
                    "at": turn.at,
                    "at_ts": candles[turn.at].ts.isoformat(),
                    "was": turn.was.value,
                    "now": turn.now.value,
                    "level": str(turn.level),
                    "balance_start": turn.balance_start,
                    "balance_end": turn.balance_end,
                },
            ),
        )
    return Layer(
        flag,
        tuple(
            {
                "kind": leg.kind.value,
                "direction": leg.direction.value,
                "start": leg.start,
                "end": leg.end,
                "low": str(leg.low),
                "high": str(leg.high),
                "volume": str(leg.volume),
                "gaps": leg.gaps,
                "displacement_atr": (
                    None if leg.displacement_atr is None else str(leg.displacement_atr)
                ),
            }
            for leg in legs
        ),
    )


LINE_FLAGS = frozenset(
    {
        # 선 자체를 그리는 레이어
        "structure.trendline",
        "structure.channel",
        # 🔴 **합류 가산으로 선을 읽는 셋업들.** 이들이 켜져 있으면 선이 없으면
        #    "합류 없음" 이 되어 근거 등급이 조용히 낮아진다 (`geometry.trendlines`
        #    · `geometry.channels` 를 읽는 곳 = order_block · fvg · trendline_channel).
        "setup.order_block",
        "setup.fvg",
        "setup.trendline_channel",
    }
)
"""추세선·채널이 **실제로 필요한** 플래그들.

🔴 **새 소비자는 여기에 자기 플래그를 더해야 한다.** 빠뜨리면 그 셋업은 선을 못 보고
   조용히 등급이 낮아진다 — 예외가 아니라 침묵으로 틀린다 (절대 규칙 #8). 그래서
   목록을 소비 지점마다 흩지 않고 **이 한 곳**에 둔다.

⚠️ 박스권 셋업 계열은 여기 없다. 박스권 매매는 수평 레벨(`level_book`)만 보므로
   선을 한 번도 읽지 않는다 — 그런데도 화면이 폴링마다 계산하고 있었다.
"""


def needs_lines(flags: "Sequence[str]") -> bool:
    """이 플래그 조합이 추세선·채널을 읽는가.

    Args:
        flags: 켜 둔 플래그 id 들. **셋업 플래그도 포함**해서 넘긴다 — 레이어로는
            안 그리지만 합류 가산으로 선을 읽는 셋업이 있다.

    Returns:
        하나라도 선을 읽으면 True.
    """
    return any(flag in LINE_FLAGS for flag in flags)


PROJECTED_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "structure.box_range",
        "structure.level",
        "structure.order_block",
        "structure.fvg",
    }
)
"""하위 축에 **얹을 수 있는** 플래그들 — 가격대만으로 정의되는 것들이다.

⛔ 추세선·채널은 넣지 않는다. **기울기가 시간 축에 묶여** 있어서, 15m 의 기울기를 10초봉
좌표에 그대로 그리면 전혀 다른 선이 된다. 그것은 투영이 아니라 조작이다.

⚠️ 지표(MA·ATX·RSI)도 넣지 않는다. 각 축에서 계산되는 값이고, 상위 축 값을 하위 축에
띄우면 그 축의 지표처럼 보인다.
"""


def project(source: FrameView, target: FrameView) -> FrameView:
    """상위 축에서 잡힌 **가격대를 하위 축 화면에 얹는다**.

    Args:
        source: 기준을 잡은 축 (진입 축 — 보통 15m).
        target: 보고 있는 축 (10s·30s·1m 등).

    Returns:
        `source` 의 가격대가 얹힌 새 `FrameView`. `target` 자체는 바뀌지 않는다.

    Note:
        🔴 **왜 필요한가** (사용자 요구 2026-08-18):

        > *"15분봉에서 잡힌 전략 기준으로 동작(진입가, 익절가, 손절가, 등등)하는 걸
        > 10초봉에서 보던가 할 수는 있을 거 아냐."*

        축마다 **자기 봉으로 따로 탐지**하는 구조였다. 그래서 10초봉에서는 아무것도 안
        나왔다 — 실측(2026-08-18):

            10s  800봉 · 가격 폭 0.654%  →  박스 0건
            30s  800봉 · 가격 폭 1.241%  →  박스 5건
            15m  800봉 · 가격 폭 4.768%  →  박스 5건

        버그가 아니다. 0.65% 안에는 유의미한 박스가 없고, 그것이 맞는 답이다. 문제는
        **질문이 달랐다는 것**이다 — 알고 싶은 것은 *"10초봉이 보는 박스"* 가 아니라
        *"15분봉 박스를 10초봉 해상도로 보기"* 다.

        ⚠️ **가격대만 옮긴다.** 박스는 `low`/`high` 로 정의되므로 축과 무관하게 유효하다.
        반면 `from_index` 는 **그 축의 봉 번호**라 그대로 옮기면 엉뚱한 곳을 가리킨다 —
        0 으로 눕혀 화면 전체에 걸친 띠로 그린다.

        ⚠️ **같은 플래그에 얹는다** (`structure.box_range`). 새 플래그를 만들면 화면·범례·
        보기설정을 다 손봐야 하고, 한 곳을 빼먹으면 조용히 안 그려진다.

        🔴 **어디서 온 값인지 `note` 에 적는다.** 안 적으면 사람이 *"10초봉이 이 박스를
        찾았다"* 고 읽고, 그것은 거짓이다 (§1-0s 관측 규약).
    """
    if source.timeframe is target.timeframe:
        return target
    borrowed: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for layer in source.layers:
        if layer.flag not in PROJECTED_FLAGS:
            continue
        moved = tuple(
            # ⛔ `from_index` 를 0 으로 눕힌다 — 상위 축 봉 번호는 하위 축에서 뜻이 없다.
            {**dict(shape), "from_index": 0, "projected": source.timeframe.value}
            for shape in layer.shapes
        )
        if moved:
            borrowed[layer.flag] = moved
    if not borrowed:
        return target

    layers: list[Layer] = []
    for layer in target.layers:
        extra = borrowed.pop(layer.flag, ())
        if not extra:
            layers.append(layer)
            continue
        # ⭐ 하위 축이 스스로 찾은 것은 **버리지 않는다** — 둘을 겹쳐 봐야 어긋남이 보인다.
        note = f"{source.timeframe.value} 기준 {len(extra)}개를 얹었다"
        if layer.shapes:
            note = f"{layer.note} · {note}" if layer.note else note
        layers.append(Layer(flag=layer.flag, shapes=layer.shapes + extra, note=note))
    # 하위 축에 그 플래그 자체가 없었던 경우 — 새로 만든다.
    for flag, extra in borrowed.items():
        layers.append(
            Layer(
                flag=flag,
                shapes=extra,
                note=f"{source.timeframe.value} 기준 {len(extra)}개 — 이 축이 찾은 것은 없다",
            )
        )
    return FrameView(
        timeframe=target.timeframe,
        candles=target.candles,
        layers=tuple(layers),
        note=target.note,
        bundle=target.bundle,
    )


def build_frame(
    candles: "Sequence[Candle]",
    timeframe: Timeframe,
    flags: "Sequence[str]",
    *,
    params: StructureParams | None = None,
    deviation: Decimal = ZIGZAG_ATR_MULTIPLE,
) -> FrameView:
    """시간축 하나를 계산한다.

    Args:
        candles: **as-of 로 이미 잘린** 봉들 (`AsOfSequence.until`).
        timeframe: 시간축.
        flags: 볼 플래그 id 들.
        params: 구조물 파라미터. None 이면 표준값.
        deviation: ZigZag 보기 배수.

    Returns:
        이 시간축의 화면.

    Note:
        봉이 모자라면 **레이어를 비우고 이유를 남긴다.** 예외를 던지면 상위 시간축
        하나 때문에 화면 전체가 죽고, 조용히 빈 레이어를 내면 "탐지 0건"으로 읽힌다.
    """
    if len(candles) < MIN_BARS:
        return FrameView(
            timeframe=timeframe,
            candles=tuple(candles),
            layers=tuple(Layer(flag, (), note="봉 부족") for flag in flags),
            note=f"봉이 {len(candles)}개뿐이다 (최소 {MIN_BARS}) — 작도를 시도하지 않았다",
        )

    settings = params or StructureParams()
    indicators = indicator_snapshot.compute(candles, settings.swing)
    # 축 J5(ZigZag 앵커)가 켜지면 ATR 이 필요하다. 항상 넘긴다 — 안 쓰면 무시된다.
    #
    # 🔴 **추세선은 아무도 안 볼 때 계산하지 않는다.** 이 창의 비용은 거의 전부
    #    추세선이다 (7.6초 중 7.43초 = 97%, `compute_bundle` docstring 실측).
    #    박스권 매매만 켠 화면은 선을 한 번도 읽지 않는데 폴링마다 3초를 태웠다.
    bundle = compute_bundle(
        candles, timeframe, settings, indicators.atr14, lines=needs_lines(flags)
    )
    return FrameView(
        timeframe=timeframe,
        candles=tuple(candles),
        # 🔴 셋업은 여기서 안 만든다 — 탐지기는 **멀티 TF 컨텍스트**를 보므로 시간축
        #    하나로 만들면 실전이 내는 것과 다른 셋업이 나온다 (`setups.py`).
        layers=tuple(
            _build_layer(flag, candles, timeframe, bundle, indicators, deviation)
            for flag in flags
            if not flag.startswith("setup.")
        ),
        bundle=bundle,
    )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """점검 한 판.

    Attributes:
        symbol: 종목 코드.
        as_of: 점검 시점.
        seed: 이 시점을 만든 시드. **재현에 필요하므로 반드시 싣는다.**
        flags: 실제로 계산한 플래그 id 들.
        frames: 시간축별 화면.
    """

    symbol: str
    as_of: datetime
    seed: int | None
    flags: tuple[str, ...]
    frames: tuple[FrameView, ...]

    def to_dict(self) -> dict[str, Any]:
        """화면이 읽을 dict 로.

        Returns:
            JSON 직렬화 가능한 dict. `Decimal` 은 문자열이다.
        """
        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "seed": self.seed,
            "flags": list(self.flags),
            "frames": [
                {
                    "timeframe": frame.timeframe.value,
                    "note": frame.note,
                    "bars": len(frame.candles),
                    # 🔴 키 이름은 프론트 `Candle` 타입과 **정확히** 같아야 한다.
                    #
                    #    처음에 백테스트 차트 규약(`o/h/l/c`)으로 냈는데 이 화면은
                    #    `Candle`(open/high/low/close)을 읽는다. 타입스크립트는 통과
                    #    했다 — 응답을 `Candle[]` 이라고 **선언만** 했지 런타임 JSON 은
                    #    다른 모양이었기 때문이다. 전부 `NaN` 이 되어 축이 무너졌고
                    #    화면에 **가운데 일직선**이 그려졌다.
                    #
                    #    타입이 거짓말을 하면 타입 검사가 아무것도 못 막는다.
                    #    `test_inspection_snapshot` 이 키 이름을 고정한다.
                    # 🔴 **모양은 `common/wire.py` 가 짓는다** (사용자 감사 2026-08-30).
                    #    전에는 여기서 손으로 지었고, 그 사전이 `/analysis/*` 의 것과
                    #    글자까지 같았다 — 같게 유지될 이유는 없었다. 이 경로가
                    #    **RUN 차트에 800봉을 보내는 가장 큰 입구**라, 여기가 빠지면
                    #    필드를 하나 더할 때 정작 제일 많이 쓰는 화면이 안 따라온다.
                    "candles": [candle_json(candle) for candle in frame.candles],
                    "layers": [
                        {
                            "flag": layer.flag,
                            "note": layer.note,
                            "count": layer.count,
                            "shapes": list(layer.shapes),
                        }
                        for layer in frame.layers
                    ],
                }
                for frame in self.frames
            ],
        }
