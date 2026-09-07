"""멀티 TF 근거 중첩 검증 (P1 §1-0m · spec §5.5 · 절대 규칙 #5·#10).

여섯 가지를 지킨다:

1. **점수는 서로 다른 TF 수** — 같은 TF 의 띠가 여러 개 겹쳐도 깊이는 1 이다 (§5.5)
2. **연결 성분으로 묶지 않는다** — A~B, B~C 가 겹쳐도 A~C 가 안 겹치면 한 덩어리가
   아니다 (single-link 연쇄 방지)
3. **역할은 위치가 정한다** — 저항 박스도 진입가 아래면 지지로 쓰인다 (S/R Flip)
4. **손절폭은 결과다** — 같은 셋업이라도 지지 배치가 다르면 손절폭이 다르다
5. **폴백을 숨기지 않는다** — 합류대가 없으면 `fell_back` 이 True 다 (절대 규칙 #8)
6. **미래 참조 없음** — `LevelLookup.at()` 은 **마감된** 봉만 돌려준다
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.structures.box import Box
from updown.analysis.structures.confluence import ZoneKind
from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.analysis.structures.zone_map import (
    ConfluentZone,
    LevelLookup,
    TimeframeZone,
    merge,
    resistance_above,
    support_below,
    zones_from_boxes,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopCandidate,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.common.domain.structure import PriceRange
from updown.decision.risk.zone_stop import (
    ConfluenceStopResolver,
    StopFloorBasis,
    TightStopPolicy,
    TooTightError,
    ZoneStopChoice,
)

ENTRY = Decimal(100)
INSTRUMENT = Instrument(
    market=Market.UPBIT,
    symbol="KRW-TEST",
    name="테스트",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)


def zone(
    timeframe: Timeframe,
    low: str,
    high: str,
    kind: ZoneKind = ZoneKind.BOX_SUPPORT,
) -> TimeframeZone:
    """띠 하나."""
    return TimeframeZone(
        timeframe=timeframe,
        kind=kind,
        low=Decimal(low),
        high=Decimal(high),
        source=f"{timeframe.value} 테스트",
        touch_count=2,
    )


def setup_at(entry: Decimal = ENTRY, stop: str = "90", target: str = "110") -> TradeSetup:
    """폴백 경로가 성립하는 최소 셋업."""
    return TradeSetup(
        setup_type="TEST",
        rule_version="test@1.0",
        entry_trigger=EntryTrigger.TOUCH,
        entry_plan=(EntryLeg(price=entry, ratio=Decimal(1)),),
        avg_entry=entry,
        stop_loss=Decimal(stop),
        stop_candidates=(
            StopCandidate(price=Decimal(stop), timeframe=Timeframe.M15, source="테스트 구조"),
        ),
        tp_ladder=(TakeProfitStep(price=Decimal(target), ratio=Decimal(1), then=None),),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
        rr_ratio=Decimal(2),
        confidence=0.5,
        evidence=(),
    )


# ---------------------------------------------------------------------------
# 1. 점수는 서로 다른 TF 수다
# ---------------------------------------------------------------------------


def test_same_timeframe_overlaps_still_count_as_depth_one() -> None:
    """§5.5 개수 세기의 함정 — 같은 시간축의 박스 3개는 근거 3개가 아니다."""
    zones = [
        zone(Timeframe.M15, "95", "97"),
        zone(Timeframe.M15, "95.5", "97.5"),
        zone(Timeframe.M15, "96", "98"),
    ]
    bands = merge(zones)
    deepest = max(bands, key=lambda band: band.zone_count)
    assert deepest.zone_count == 3
    assert deepest.depth == 1, "같은 TF 는 몇 개가 겹쳐도 근거 1 개다"


def test_different_timeframes_raise_the_depth() -> None:
    """서로 다른 시간축이 같은 가격대를 가리키면 그것이 중첩이다."""
    zones = [
        zone(Timeframe.M15, "95", "97"),
        zone(Timeframe.H1, "94", "98"),
        zone(Timeframe.D1, "96", "99"),
    ]
    band = max(merge(zones), key=lambda item: item.depth)
    assert band.depth == 3
    assert band.timeframes == (Timeframe.M15, Timeframe.H1, Timeframe.D1), (
        "시간축 정렬은 문자열이 아니라 간격 기준이어야 한다"
    )


def test_timeframes_sort_by_interval_not_by_string() -> None:
    """`StrEnum` 을 문자열로 정렬하면 `15m < 1h < 4h < 5m` 이 된다."""
    band = ConfluentZone(
        low=Decimal(95),
        high=Decimal(97),
        zones=(zone(Timeframe.H4, "94", "98"), zone(Timeframe.M5, "95", "97")),
    )
    assert band.timeframes == (Timeframe.M5, Timeframe.H4)


# ---------------------------------------------------------------------------
# 2. 연결 성분으로 묶지 않는다
# ---------------------------------------------------------------------------


def test_chained_zones_do_not_form_one_band() -> None:
    """A~B 와 B~C 가 겹쳐도 A~C 가 안 겹치면 셋을 한 덩어리로 세지 않는다.

    Note:
        single-link 연쇄를 허용하면 넓은 띠 하나가 서로 다른 가격대를 이어 붙여 깊이를
        부풀린다 — `box._cluster` 가 경고한 것과 같은 함정이다.
    """
    zones = [
        zone(Timeframe.M15, "90", "94"),
        zone(Timeframe.H1, "93", "97"),
        zone(Timeframe.D1, "96", "100"),
    ]
    bands = merge(zones)
    assert all(band.depth <= 2 for band in bands), (
        "90~94 와 96~100 은 겹치지 않으므로 깊이 3 이 나오면 안 된다"
    )
    assert any(band.depth == 2 for band in bands), "인접한 둘은 실제로 겹친다"


def test_isolated_zone_survives_as_depth_one() -> None:
    """깊이 1 을 버리면 "중첩 없는 진입"과 비교할 대조군이 사라진다."""
    bands = merge([zone(Timeframe.M15, "90", "92"), zone(Timeframe.H1, "95", "97")])
    assert len(bands) == 2
    assert {band.depth for band in bands} == {1}


# ---------------------------------------------------------------------------
# 3. 역할은 위치가 정한다 (S/R Flip)
# ---------------------------------------------------------------------------


def test_resistance_box_below_entry_acts_as_support() -> None:
    """spec §4.3.1 S/R Flip — 사용자 차트의 `이전 저항` 이 지지 근거였다."""
    bands = merge([zone(Timeframe.H1, "94", "96", ZoneKind.BOX_RESISTANCE)])
    below = support_below(ENTRY, bands)
    assert len(below) == 1
    assert below[0].kinds == (ZoneKind.BOX_RESISTANCE,), "종류는 지우지 않고 기록해 둔다"


def test_band_containing_entry_is_not_a_resistance() -> None:
    """서 있는 자리는 지지이지 저항이 아니다 — 비대칭이 실제다."""
    bands = merge([zone(Timeframe.H1, "99", "101")])
    assert support_below(ENTRY, bands), "진입가를 품은 띠는 지지로 잡힌다"
    assert not resistance_above(ENTRY, bands), "이미 통과한 레벨은 익절 목표가 될 수 없다"


# ---------------------------------------------------------------------------
# 4·5. 손절폭은 결과이고, 폴백은 숨기지 않는다
# ---------------------------------------------------------------------------


def test_closer_support_yields_a_tighter_stop() -> None:
    """같은 셋업·같은 ATR 인데 배치가 다르면 손절폭이 다르다 — 그것이 §1-0m 의 핵심이다."""
    resolver = ConfluenceStopResolver()
    atr, margin = Decimal(2), Decimal(1)

    near = resolver.resolve(setup_at(), merge([zone(Timeframe.H1, "97", "99")]), atr, margin)
    far = resolver.resolve(setup_at(), merge([zone(Timeframe.H1, "88", "92")]), atr, margin)

    assert near.stop == Decimal(96), "97 - 1"
    assert far.stop == Decimal(87), "88 - 1"
    assert ENTRY - near.stop < ENTRY - far.stop
    assert not near.fell_back and not far.fell_back


def test_missing_band_falls_back_and_records_it() -> None:
    """조용히 넘어가면 "축 K 가 효과 없다"와 "대부분 폴백이었다"가 구분되지 않는다."""
    resolver = ConfluenceStopResolver()
    result = resolver.resolve(setup_at(), [], Decimal(2), Decimal(1))
    assert result.fell_back is True
    assert result.depth == 0, "폴백은 깊이 0 — 깊이 1 과 구분된다"
    # 폴백은 max(구조 10, 2.0 x ATR 2 = 4) = 10 → 진입 100 - 10
    assert result.stop == Decimal(90)


def test_axis_k_candidates_disagree() -> None:
    """후보가 같은 값을 내면 경쟁이 성립하지 않는다 (§5.6.7)."""
    zones = [
        zone(Timeframe.M15, "97", "99"),  # 가깝지만 얕다
        zone(Timeframe.H1, "92", "95"),  # 멀지만 깊다
        zone(Timeframe.D1, "93", "96"),
    ]
    bands = merge(zones)
    atr, margin = Decimal(2), Decimal(1)
    answers = {
        choice: ConfluenceStopResolver(choice).resolve(setup_at(), bands, atr, margin).stop
        for choice in ZoneStopChoice
    }
    assert answers[ZoneStopChoice.NEAREST_WITH_BUFFER] == Decimal(96), "가장 가까운 97 - 여유 1"
    assert answers[ZoneStopChoice.NEAREST_TIGHT] == Decimal(97), "가장 가까운 97, 여유 없음"
    assert answers[ZoneStopChoice.DEEPEST] == Decimal(92), "1h+1d 교집합(하단 93) - 여유 1"
    assert answers[ZoneStopChoice.NEAREST_CONFLUENT] == Decimal(92), (
        "K4 — 얕은 15m 띠(97)를 건너뛰고 깊이 2 인 1h+1d 교집합(하단 93)에서 여유 1"
    )
    # K2 는 최소 손절폭이 들어온 뒤 K1 과 같은 답을 내는 구간이 생겼다(퇴역). 그래서
    # 전부 다름이 아니라 **의미 있는 후보들이 갈리는지**를 본다.
    live = {
        ZoneStopChoice.NEAREST_WITH_BUFFER,
        ZoneStopChoice.DEEPEST,
        ZoneStopChoice.NEAREST_CONFLUENT,
    }
    assert len({answers[choice] for choice in live}) >= 2, (
        "활성 후보가 전부 같은 답을 내면 경쟁이 성립하지 않는다"
    )


def test_target_stops_short_of_resistance() -> None:
    """저항은 도달점이 아니라 나오는 지점이다."""
    bands = merge([zone(Timeframe.H1, "95", "97"), zone(Timeframe.H1, "108", "112")])
    result = ConfluenceStopResolver().resolve(setup_at(), bands, Decimal(2), Decimal(1))
    assert result.target == Decimal(107), "108 - 1 — 저항 하단에 닿기 전"
    assert result.resistance is not None


def test_target_is_left_to_structure_not_pushed_to_a_floor() -> None:
    """익절에는 **하한을 걸지 않는다** — 구조가 정하고, 손익비가 안 나오면 진입을 포기한다.

    Note:
        🔴 손절과 같은 하한을 익절에도 걸었다가 실측에서 **RR 이 전부 정확히 1.00** 이
        됐다 (BTC/ETH 1h 전 거래). 구조에서 도출한 값이 둘 다 하한보다 좁아, 합류대를
        안 보고 `진입 ± 하한` 으로 대칭 진입한 셈이 됐다. 하한이 1xATR 이라 **봉 하나
        안에 양쪽이 다 들어가** 보유 0봉으로 끝나기까지 했다.

        억지로 민 목표는 근거 없는 숫자다. 목표는 구조가 정하고, 그 결과 RR 이 안
        나오면 **호출부가 진입을 포기**한다 (`MIN_FIRST_RR`).
    """
    bands = merge([zone(Timeframe.H1, "95", "97"), zone(Timeframe.H1, "100.5", "102")])
    result = ConfluenceStopResolver().resolve(setup_at(), bands, Decimal(2), Decimal(1))
    # 저항 하단 100.5 - 여유 1 = 99.5 로 진입가 아래 → 셋업 제안으로 되돌린다
    assert result.target == Decimal(110), "셋업의 tp_ladder[0]"
    assert result.widened_target is False, "익절 하한은 없다"
    assert result.resistance is None, "근거로 쓰지 않았으면 근거에서 지운다"


@pytest.mark.parametrize("choice", list(ZoneStopChoice))
@pytest.mark.parametrize(
    "layout",
    [
        [],  # 폴백
        [("99.9", "99.95")],  # 진입가 바로 아래
        [("99", "101")],  # 진입가를 품는다
        [("101", "105")],  # 전부 위 — 지지가 없다
        [("50", "60"), ("98", "99"), ("101", "103")],
    ],
)
def test_resolved_stop_is_always_below_entry(
    choice: ZoneStopChoice, layout: list[tuple[str, str]]
) -> None:
    """롱 온리 전제 (절대 규칙 #10) — 배치가 어떻든 깨지지 않아야 한다.

    Note:
        `stop >= entry` 예외를 직접 유발하는 테스트는 쓰지 않았다. `support_below` 가
        `low < entry` 인 띠만 돌려주므로 현재 구현에서는 **도달할 수 없는 분기**이고,
        억지로 유발하려면 여유를 음수로 넣어야 한다 — 그것은 계약 위반이지 시나리오가
        아니다. 대신 **불변식 자체**를 여러 배치에서 확인한다.
    """
    bands = merge([zone(Timeframe.H1, low, high) for low, high in layout])
    try:
        result = ConfluenceStopResolver(choice).resolve(setup_at(), bands, Decimal(2), Decimal(1))
    except TooTightError:
        # 🔴 축 P3(기본값)은 구조 손절이 최소 손절폭보다 좁으면 **진입을 포기**한다.
        #    포기는 롱 온리 위반이 아니다 — 애초에 포지션을 안 잡는다. `99.9~99.95`
        #    처럼 진입가 바로 아래에 얇은 띠만 있는 배치가 정확히 그 경우다.
        return
    assert result.stop < ENTRY
    assert result.target > ENTRY, "익절이 진입가 이하면 RR 이 음수가 된다"


def test_rr_is_a_derived_result() -> None:
    """RR 을 입력으로 두면 손절·익절이 그 숫자를 맞추려고 움직인다."""
    bands = merge([zone(Timeframe.H1, "94", "96"), zone(Timeframe.H1, "108", "112")])
    result = ConfluenceStopResolver().resolve(setup_at(), bands, Decimal(2), Decimal(1))
    # 손절 93 · 익절 107 · 진입 100 → (107-100)/(100-93)
    assert result.rr(ENTRY) == Decimal(1)


# ---------------------------------------------------------------------------
# 6. 미래를 보지 않는다
# ---------------------------------------------------------------------------


def _candles(count: int, timeframe: Timeframe, step: timedelta) -> list[Candle]:
    """지그재그 캔들 — 스윙이 실제로 잡히게 만든다."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    for index in range(count):
        base = Decimal(100) + (Decimal(5) if index % 4 in (1, 2) else Decimal(0))
        rows.append(
            Candle(
                instrument=INSTRUMENT,
                timeframe=timeframe,
                ts=start + step * index,
                open=base,
                high=base + Decimal(1),
                low=base - Decimal(1),
                close=base,
                volume=Decimal(10),
            )
        )
    return rows


def test_lookup_returns_only_closed_bars() -> None:
    """봉 시작 시각으로 색인하면 **진행 중인** 상위 봉을 보게 된다 — 미래 참조다."""
    step = timedelta(hours=1)
    candles = _candles(30, Timeframe.H1, step)
    lookup = LevelLookup.build(candles, Timeframe.H1, lookback_bars=10)

    first_close = candles[0].ts + step
    assert lookup.at(first_close - timedelta(minutes=1)) == (), "마감 전에는 아무것도 없다"
    assert lookup.at(first_close) is lookup.zones[0], "마감 시각에 그 봉이 확정된다"
    assert lookup.at(candles[-1].ts + step) is lookup.zones[-1]


def test_unchanged_bars_share_the_same_tuple() -> None:
    """1h 3년치 x 봉당 띠 목록이면 메모리가 문제가 된다."""
    candles = _candles(40, Timeframe.H1, timedelta(hours=1))
    lookup = LevelLookup.build(candles, Timeframe.H1, lookback_bars=10)
    shared = sum(
        1 for before, after in zip(lookup.zones, lookup.zones[1:], strict=False) if before is after
    )
    assert shared > 0, "연속한 봉이 같은 레벨을 내면 객체를 공유해야 한다"


def test_box_to_zone_applies_margin_on_both_sides() -> None:
    """`confluence._zones` 의 박스 처리와 같은 폭이어야 한다."""
    box = Box(
        kind=SwingKind.LOW,
        price_range=PriceRange(low=Decimal(95), high=Decimal(97)),
        level=Decimal(96),
        touches=(
            SwingPoint(
                index=0,
                ts=datetime(2026, 1, 1, tzinfo=UTC),
                price=Decimal(95),
                kind=SwingKind.LOW,
            ),
        ),
        first_ts=datetime(2026, 1, 1, tzinfo=UTC),
        last_ts=datetime(2026, 1, 1, tzinfo=UTC),
    )
    made = zones_from_boxes(Timeframe.H1, [box], Decimal("0.5"))
    assert (made[0].low, made[0].high) == (Decimal("94.5"), Decimal("97.5"))
    assert made[0].kind is ZoneKind.BOX_SUPPORT


# ---------------------------------------------------------------------------
# 5. 하한이 **어느 쪽**이 물었는가 — 처방이 정반대다 (§1-0t T9)
# ---------------------------------------------------------------------------


def test_no_floor_means_the_stop_is_what_axis_k_chose() -> None:
    """하한이 안 물어야 그 손절이 축 K 의 답이다 — 깊이 표 해석의 전제."""
    # 지지 97 → 손절 96. 하한은 ATR 1x = 2 (98), 비용 0 → 둘 다 96 보다 위가 아니다.
    result = ConfluenceStopResolver().resolve(
        setup_at(), merge([zone(Timeframe.H1, "97", "99")]), atr=Decimal(1), margin=Decimal(1)
    )

    assert result.floor_basis is StopFloorBasis.STRUCTURE
    assert not result.widened_stop
    assert result.stop == Decimal(96)


def test_atr_floor_binding_says_the_timeframe_is_too_low() -> None:
    """🔴 구조가 **봉 하나보다 촘촘**하면 ATR 하한이 문다 — 시간축을 올려 풀 문제다."""
    # 지지 99 → 손절 98.5 인데 ATR 1x = 5 라 하한이 95 로 밀어낸다.
    result = ConfluenceStopResolver(tight=TightStopPolicy.WIDEN).resolve(
        setup_at(),
        merge([zone(Timeframe.H1, "99", "99.5")]),
        atr=Decimal(5),
        margin=Decimal("0.5"),
        round_trip_pct=Decimal("0.001"),  # 비용 하한 0.2 — ATR 보다 훨씬 작다
    )

    assert result.floor_basis is StopFloorBasis.ATR
    assert result.stop == Decimal(95), "100 - 1xATR(5)"


def test_cost_floor_binding_says_the_market_cannot_pay() -> None:
    """🔴 구조가 **비용을 못 갚으면** 비용 하한이 문다 — 어떤 승률로도 안 풀린다."""
    # ATR 하한 0.5 vs 비용 하한 2x0.02x100 = 4 → 비용이 이긴다.
    result = ConfluenceStopResolver(tight=TightStopPolicy.WIDEN).resolve(
        setup_at(),
        merge([zone(Timeframe.H1, "99", "99.5")]),
        atr=Decimal("0.5"),
        margin=Decimal("0.1"),
        round_trip_pct=Decimal("0.02"),
    )

    assert result.floor_basis is StopFloorBasis.COST
    assert result.stop == Decimal(96), "100 - 2x왕복비용(4)"


def test_the_bigger_floor_wins_and_there_is_no_both_bucket() -> None:
    """둘 다 넘어도 **더 큰 쪽**만 기록한다 — "둘 다" 칸은 원인을 다시 흐린다."""
    # ATR 하한 8 vs 비용 하한 4 → ATR 이 손절 위치를 정한다.
    result = ConfluenceStopResolver(tight=TightStopPolicy.WIDEN).resolve(
        setup_at(),
        merge([zone(Timeframe.H1, "99", "99.5")]),
        atr=Decimal(8),
        margin=Decimal("0.1"),
        round_trip_pct=Decimal("0.02"),
    )

    assert result.floor_basis is StopFloorBasis.ATR
    assert result.stop == Decimal(92)


def tight_and_wide() -> list[ConfluentZone]:
    """가까운 지지는 너무 좁고, 더 아래에 넉넉한 지지가 하나 더 있는 배치."""
    return merge(
        [
            zone(Timeframe.M15, "99", "99.5"),  # 손절 98.5 — 하한(95)보다 좁다
            zone(Timeframe.H1, "93", "94"),  # 손절 92.5 — 하한을 만족한다
        ]
    )


def test_p1_widens_to_the_floor_and_loses_the_structure() -> None:
    """P1(현행) — 손절이 `진입 - 하한` 이 되어 **가격 구조와 무관한 숫자**가 된다."""
    result = ConfluenceStopResolver(tight=TightStopPolicy.WIDEN).resolve(
        setup_at(), tight_and_wide(), atr=Decimal(5), margin=Decimal("0.5")
    )

    assert result.stop == Decimal(95), "진입 100 - 1xATR(5)"
    assert result.floor_basis is StopFloorBasis.ATR
    assert result.support is not None
    assert result.support.low == Decimal(99), "가까운 띠를 고른 뒤 하한으로 밀었다"


def test_p2_skips_instead_of_pushing_the_stop() -> None:
    """P2 — 익절 쪽과 **대칭**이다. 억지로 민 손절은 근거 없는 숫자다."""
    with pytest.raises(TooTightError, match="축 P2"):
        ConfluenceStopResolver(tight=TightStopPolicy.SKIP).resolve(
            setup_at(), tight_and_wide(), atr=Decimal(5), margin=Decimal("0.5")
        )


def test_p3_finds_the_next_support_down() -> None:
    """🔴 **사용자가 말한 것** — 하한으로 때우지 말고 더 아래 전저점을 본다."""
    result = ConfluenceStopResolver(tight=TightStopPolicy.NEXT_SUPPORT).resolve(
        setup_at(), tight_and_wide(), atr=Decimal(5), margin=Decimal("0.5")
    )

    assert result.stop == Decimal("92.5"), "93 - 여유 0.5 — 구조에서 나온 값이다"
    assert result.floor_basis is StopFloorBasis.STRUCTURE, "하한이 물지 않았다"
    assert result.support is not None
    assert result.support.low == Decimal(93), "가까운 띠를 건너뛰고 아래 것을 골랐다"


def test_p3_gives_up_when_no_support_is_wide_enough() -> None:
    """끝까지 없으면 포기한다 — 거기서 넓히면 다시 근거 없는 숫자가 된다."""
    only_tight = merge([zone(Timeframe.M15, "99", "99.5")])

    with pytest.raises(TooTightError, match="축 P3"):
        ConfluenceStopResolver(tight=TightStopPolicy.NEXT_SUPPORT).resolve(
            setup_at(), only_tight, atr=Decimal(5), margin=Decimal("0.5")
        )


def test_axis_p_does_not_replace_axis_k() -> None:
    """P3 은 후보를 **거른 뒤** 축 K 규칙을 적용한다 — 두 축이 직교한다."""
    bands = merge(
        [
            zone(Timeframe.M15, "99", "99.5"),  # 좁다 → P3 이 거른다
            zone(Timeframe.M15, "93", "94"),  # 넉넉하지만 얕다 (깊이 1)
            zone(Timeframe.H1, "88", "89"),
            zone(Timeframe.H4, "88.5", "89.5"),  # 넉넉하고 깊다 (깊이 2)
        ]
    )
    args = {"atr": Decimal(5), "margin": Decimal("0.5")}

    k1 = ConfluenceStopResolver(tight=TightStopPolicy.NEXT_SUPPORT).resolve(
        setup_at(), bands, **args
    )
    k4 = ConfluenceStopResolver(
        ZoneStopChoice.NEAREST_CONFLUENT, tight=TightStopPolicy.NEXT_SUPPORT
    ).resolve(setup_at(), bands, **args)

    assert k1.stop == Decimal("92.5"), "K1 = 거른 것 중 가장 가까운 93 - 여유 0.5"
    # 합류대는 **교집합**이라 88~89 와 88.5~89.5 가 겹친 띠의 하단은 88.5 다.
    assert k4.stop == Decimal("88.0"), "K4 = 거른 것 중 깊이 2+ 인 띠(88.5) - 여유 0.5"
    assert k1.stop != k4.stop, "축 K 가 여전히 살아 있어야 축 P 와 직교한다"


def test_p3_is_the_default_after_the_v3_verdict() -> None:
    """✅ **v3 판정 결과** (2026-08-09) — 기본값이 P1 → P3 로 바뀌었다.

    Note:
        🔴 **성과로 이긴 것이 아니다.** 돌파에서 P1 +0.878R vs P3 +0.722R 로 오히려
        P1 이 높았는데, n≈45 에 표준오차가 대략 0.15R 이라 **1 SE 차이는 차이가 아니다** —
        선언한 반증 조건("유의하게 낮으면 기각")은 발동하지 않았다.

        바꾼 이유는 **P1 이 축 K 를 못 재게 하기 때문**이다. 눌림목 P1 에서 손절의
        **96.9%** 가 하한(`진입 - max(1xATR, 2x비용)`)이었고, 그것은 시장이 지키는
        자리가 아니라 가격 구조와 무관한 숫자다.

        이 테스트가 깨지면 **기본값을 되돌린 것**이므로 `docs/rules/rule_candidates.md` 축 P
        의 판정을 함께 갱신해야 한다.
    """
    assert ConfluenceStopResolver().tight is TightStopPolicy.NEXT_SUPPORT


# ---------------------------------------------------------------------------
# 축 T — 비용 커버 배수 (사용자 룰북 STEP 8-3 대조)
# ---------------------------------------------------------------------------


def test_cost_cover_multiple_moves_the_floor() -> None:
    """배수를 올리면 최소 손절폭이 그만큼 넓어진다.

    Note:
        🔴 이것이 축 T 의 전부다. 배수 2 는 "동전 던지기보다 나은가"의 경계이고
        (비용/1R = 0.5), 룰북(STEP 8-3)이 요구하는 손절폭 1.5% 는 업비트 왕복
        0.157% 기준 **배수 9.5** 에 해당한다. 우리 하한이 사람이 쓰는 기준의
        1/5 였다는 뜻이다.

        배수와 비용 부담은 역수다 — `비용/1R = 1/배수`.
    """
    # 왕복 1% · 진입 100 → 배수 2 면 하한 2, 배수 10 이면 하한 10.
    round_trip = Decimal("0.01")
    # 🔴 지지대를 **진입가 바로 아래**에 둔다. 하한은 최소 폭이라, 구조 손절이 이미
    #    더 넓으면 물지 않는다 (처음 이 테스트를 그렇게 짜서 통과하지 않았다).
    #    여기서는 구조 손절폭이 0.75 라 배수 2(하한 2)에도 좁아 하한이 이긴다.
    bands = merge([zone(Timeframe.H1, "99.5", "99.8")])
    args = {"atr": Decimal("0.5"), "margin": Decimal("0.25")}

    loose = ConfluenceStopResolver(tight=TightStopPolicy.WIDEN).resolve(
        setup_at(), bands, round_trip_pct=round_trip, cost_cover=Decimal(2), **args
    )
    strict = ConfluenceStopResolver(tight=TightStopPolicy.WIDEN).resolve(
        setup_at(), bands, round_trip_pct=round_trip, cost_cover=Decimal(10), **args
    )

    assert ENTRY - loose.stop == Decimal(2), "배수 2 → 하한 2 (= 100 x 0.01 x 2)"
    assert ENTRY - strict.stop == Decimal(10), "배수 10 → 하한 10"
    assert loose.floor_basis is StopFloorBasis.COST
    assert strict.floor_basis is StopFloorBasis.COST


def test_cost_cover_default_is_the_survival_line() -> None:
    """기본값은 **생존선 2배** 그대로다 — 승자가 정해지기 전에 굳히지 않는다.

    Note:
        이 테스트가 깨지면 기본값을 바꾼 것이므로 `docs/rules/rule_candidates.md` 축 T 의
        판정을 함께 갱신해야 한다 (절대 규칙 #12).
    """
    from updown.decision.risk.zone_stop import COST_COVER_MULTIPLE

    assert Decimal(2) == COST_COVER_MULTIPLE


def test_cost_cover_must_be_positive() -> None:
    """0 이하 배수는 하한을 없애는 것이라 조용히 통과시키지 않는다 (절대 규칙 #8)."""
    with pytest.raises(ValueError, match="cost_cover"):
        ConfluenceStopResolver().resolve(
            setup_at(),
            merge([zone(Timeframe.H1, "97", "99")]),
            atr=Decimal(1),
            margin=Decimal("0.5"),
            cost_cover=Decimal(0),
        )
