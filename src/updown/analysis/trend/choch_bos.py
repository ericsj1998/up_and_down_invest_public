"""DOWN→UP 3단계 전환 판정 — CHoCH → BOS → 200MA (P1-4-4·5·6 · spec §4.16).

## 돌파는 **봉마감(종가)** 기준이다 ⚠️

spec §4.2: "탐지는 봉마감 기준". 구조 돌파에 종가를 쓰는 것은 그 조문의 적용이면서
동시에 **꼬리 돌파와 구조 돌파를 구분하는 장치**다:

| 형태 | 의미 |
|------|------|
| 꼬리만 레벨을 넘고 종가는 못 넘음 | **유동성 스윕** — 구조 돌파가 아니다 |
| 종가가 레벨을 넘음 | **구조 돌파** (CHoCH / BOS) |

이 구분이 §4.16 의 1단계 보조 확인과 정확히 맞물린다 — 전저점의 **아래꼬리 스윕 후
회복**이 신뢰도 가산이고, 그것은 "꼬리는 넘었지만 종가는 되돌아왔다"의 다른 표현이다.
같은 규칙으로 둘을 다 표현할 수 있다는 것이 이 선택의 근거다.

## 왜 3단계인가 — 빠른 신호와 느린 신호의 트레이드오프

CHoCH 는 빠르지만 가짜가 많고, 200일선은 느리지만 견고하다. §4.16 은 어느 하나를 고르지
않고 **단계별 리스크 크기로** 흡수한다 (CHoCH=진입 차단 / BOS=리스크 절반 / 200MA=정상).
그래서 이 모듈은 "전환됨/안 됨"이 아니라 **도달한 단계**를 돌려준다.

## 순서를 강제한다

BOS 는 CHoCH **이후**의 사건이어야 하고, HL 은 CHoCH 이후에 형성된 저점이어야 한다.
순서를 안 보면 하락 도중의 아무 반등이나 BOS 로 세어진다.

## 단계는 **현재 하락 국면 안에서만** 센다 ⚠️ `since_index`

`since_index` 이전의 스윙은 보지 않는다. 이것이 없으면 과거에 한 번 CHoCH+BOS+MA 가
성립한 뒤로 **영구히 3단계를 보고**하고, 구조가 무너져 DOWN 으로 떨어져도 다음 봉에 즉시
UP 으로 복귀한다 — P1-4 개발 중 실제로 5m 픽스처에서 **1봉 간격 왕복**이 나왔다.

의미상으로도 이쪽이 맞다. 상승 구조가 무너졌다면(직전 HL 하향 이탈) 그 BOS 는 **더 이상
유효한 근거가 아니다.** `stage` 는 "역사상 언젠가 있었던 신호"가 아니라 **"지금 하락
국면에서 전환이 얼마나 진행됐는가"** 다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import fmean

from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.common.domain.candle import Candle
from updown.common.domain.trend import TrendStage


@dataclass(frozen=True, slots=True)
class TransitionReading:
    """3단계 전환 판정 결과.

    Attributes:
        stage: 도달한 단계.
        choch_index: CHoCH 가 확정된 봉 번호. 없으면 None.
        bos_index: BOS 가 확정된 봉 번호. 없으면 None.
        broken_lh: CHoCH 에서 돌파된 직전 LH. 없으면 None.
        higher_low: BOS 의 근거가 된 HL. 없으면 None.
        liquidity_swept: CHoCH 직전 전저점 스윕 여부 (신뢰도 가산).
        above_ma200: 마지막 봉 종가가 200 SMA 위인가. 산출 불가면 None.
        volume_expanding: 반등 구간 거래량이 하락 구간보다 큰가. 산출 불가면 None.
    """

    stage: TrendStage
    choch_index: int | None = None
    bos_index: int | None = None
    broken_lh: SwingPoint | None = None
    higher_low: SwingPoint | None = None
    liquidity_swept: bool = False
    above_ma200: bool | None = None
    volume_expanding: bool | None = None


def find_last_lower_high(swings: Sequence[SwingPoint]) -> SwingPoint | None:
    """가장 최근의 **LH**(직전 고점보다 낮은 고점)를 찾는다.

    Args:
        swings: `prior_swings()` 결과.

    Returns:
        마지막 LH. 없으면 None.

    Note:
        하락추세는 LH/LL 의 연속이고, CHoCH 는 **그 LH 를 상향 돌파**하는 사건이다
        (spec §4.16 1단계). 단순히 "마지막 고점"을 쓰면 상승 중의 고점도 돌파 대상이
        되어 CHoCH 가 의미를 잃는다.
    """
    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]
    for position in range(len(highs) - 1, 0, -1):
        if highs[position].price < highs[position - 1].price:
            return highs[position]
    return None


def _swept_prior_low(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    before_index: int,
) -> bool:
    """`before_index` 이전에 전저점 유동성 스윕이 있었는가 (spec §4.16 1단계 보조).

    Note:
        스윕 = **아래꼬리가 전저점을 깼는데 종가는 그 위로 회복**한 봉. Wyckoff 의
        Spring 이며 강의 4장 유동성 개념과 같은 계열이다. 꼬리와 종가를 나눠 보는
        규칙이 모듈 docstring 의 돌파 정의와 대칭을 이룬다.
    """
    lows = [swing for swing in swings if swing.kind is SwingKind.LOW and swing.index < before_index]
    if len(lows) < 2:
        return False
    target = lows[-2].price  # 스윕 대상은 **직전** 전저점이다
    start = lows[-2].index + 1
    return any(
        candles[position].low < target <= candles[position].close
        for position in range(start, min(before_index + 1, len(candles)))
    )


def _first_close_above(
    candles: Sequence[Candle],
    level: Decimal,
    start_index: int,
) -> int | None:
    """`start_index` 이후 처음으로 **종가**가 `level` 을 넘는 봉 번호."""
    for position in range(max(start_index, 0), len(candles)):
        if candles[position].close > level:
            return position
    return None


def _volume_expanding(
    candles: Sequence[Candle],
    decline_end: int,
    rally_start: int,
) -> bool | None:
    """하락 구간 대비 반등 구간 거래량이 늘었는가 (spec §4.16 3단계 보조).

    Note:
        §4.16 은 "하락 시 감소 → 반등 시 증가"를 보조 확인으로 든다. 두 구간의 평균을
        비교하며, 어느 쪽이든 봉이 없으면 None 이다 — 0 으로 채우면 "거래량이 줄었다"로
        잘못 읽힌다.
    """
    decline = [c.volume for c in candles[max(decline_end - 20, 0) : decline_end]]
    rally = [c.volume for c in candles[rally_start:]]
    if not decline or not rally:
        return None
    return fmean(rally) > fmean(decline)


def evaluate_transition(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    ma200: Sequence[Decimal | None],
    since_index: int = 0,
) -> TransitionReading:
    """DOWN→UP 전환이 어느 단계까지 왔는지 판정한다 (spec §4.16).

    Args:
        candles: `ts` 오름차순 캔들. 마지막 봉이 판정 기준이다.
        swings: `prior_swings()` 결과.
        ma200: 200 SMA 시리즈. 캔들과 같은 길이여야 한다.
        since_index: **현재 하락 국면이 시작된 봉 번호.** 이전 스윙은 보지 않는다
            (모듈 docstring).

    Returns:
        도달 단계와 근거. 신호가 없으면 `stage=NONE` 이다 — **0단계도 유효한 답**이며
        없는 전환을 지어내지 않는다 (spec §4.20).

    Note:
        단계는 **누적**이다. BOS 가 성립하면 CHoCH 도 성립한 상태이고, 3단계는 BOS 위에
        200일선 조건이 더해진 것이다. 200일선만 넘고 구조가 안 바뀐 상태를 3단계로
        보고하지 않는다 — 그것은 하락추세 중의 일시적 되돌림이다.
    """
    if not candles:
        return TransitionReading(stage=TrendStage.NONE)

    last = len(candles) - 1
    reference = ma200[last] if last < len(ma200) else None
    above: bool | None = None if reference is None else candles[last].close > reference

    in_phase = [swing for swing in swings if swing.index >= since_index]
    lower_high = find_last_lower_high(in_phase)
    if lower_high is None:
        return TransitionReading(stage=TrendStage.NONE, above_ma200=above)

    choch_index = _first_close_above(candles, lower_high.price, lower_high.index + 1)
    if choch_index is None:
        return TransitionReading(stage=TrendStage.NONE, broken_lh=lower_high, above_ma200=above)

    swept = _swept_prior_low(candles, in_phase, choch_index)
    reading = TransitionReading(
        stage=TrendStage.CHOCH,
        choch_index=choch_index,
        broken_lh=lower_high,
        liquidity_swept=swept,
        above_ma200=above,
    )

    # 2단계 — CHoCH **이후**에 형성된 HL + 그 뒤 고점 재돌파
    lows_after = [
        swing for swing in in_phase if swing.kind is SwingKind.LOW and swing.index > choch_index
    ]
    higher_low: SwingPoint | None = None
    for candidate in lows_after:
        earlier = [
            swing
            for swing in in_phase
            if swing.kind is SwingKind.LOW and swing.index < candidate.index
        ]
        if earlier and candidate.price > earlier[-1].price:
            higher_low = candidate
            break
    if higher_low is None:
        return reading

    interim_highs = [
        swing
        for swing in in_phase
        if swing.kind is SwingKind.HIGH and choch_index <= swing.index < higher_low.index
    ]
    if not interim_highs:
        return reading
    bos_index = _first_close_above(
        candles, max(swing.price for swing in interim_highs), higher_low.index + 1
    )
    if bos_index is None:
        return reading

    volume = _volume_expanding(candles, choch_index, choch_index)
    reading = TransitionReading(
        stage=TrendStage.BOS,
        choch_index=choch_index,
        bos_index=bos_index,
        broken_lh=lower_high,
        higher_low=higher_low,
        liquidity_swept=swept,
        above_ma200=above,
        volume_expanding=volume,
    )

    # 3단계 — 200일선 재탈환 (구조 확립 위에 얹힌다)
    if above:
        return TransitionReading(
            stage=TrendStage.MA_RECLAIM,
            choch_index=choch_index,
            bos_index=bos_index,
            broken_lh=lower_high,
            higher_low=higher_low,
            liquidity_swept=swept,
            above_ma200=above,
            volume_expanding=volume,
        )
    return reading
