"""RSI(14) + 다이버전스 (P1-2-4 · spec §6.1).

## RSI 는 float 이다 — 계산은 Decimal 로 한다

`Indicators.rsi14` 는 `float` 이다 (0~100 무차원). 하지만 **중간 계산은 Decimal** 로
한다 — 상승분·하락분의 Wilder 평활은 가격 차이의 누적이고, 그것을 float 로 하면
평활이 재귀식이라 오차가 뒤로 누적된다. 마지막에 한 번만 float 로 바꾼다.

## 다이버전스는 전저점·전고점 정의를 그대로 쓴다

spec §6.1: "다이버전스(가격 신저점 vs RSI 저점 상승)". "신저점"은 곧 **전저점 대비
더 낮은 저점**이고, 그 정의는 `structures.swing.prior_swings()`(zigzag)가 이미 갖고
있다 (spec §6.4).

P1-1 에서 스윙 출력을 두 갈래로 나눈 것이 여기서 값을 한다 — 다이버전스에는 교대
정리된 열(`prior_swings`)이 맞고, 추세선에는 전체 극값(`find_pivots`)이 맞다.
구조물 작도용 극값을 쓰면 같은 봉우리의 여러 점이 각각 "신저점"으로 세어진다.

## 하락 다이버전스는 진입 근거가 아니다

롱 온리이므로 `BEARISH` 다이버전스는 **청산·회피·익절 근거**로만 쓴다
(spec §12.8, 절대 규칙 #10). 탐지는 하되 진입에 쓰지 않는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from updown.analysis.indicators.series import (
    SeriesError,
    require_period,
    wilder_average,
)
from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.common.numeric import fixed_context

STANDARD_PERIOD = 14
"""spec §6.1 이 지정한 RSI 기간. Wilder 원문 값이며 조정하지 않는다 (spec §5.6.2)."""

_HUNDRED = Decimal(100)


class DivergenceKind(StrEnum):
    """다이버전스 종류.

    Attributes:
        BULLISH: 가격은 신저점인데 RSI 저점은 상승 — 하락 동력 약화.
        BEARISH: 가격은 신고점인데 RSI 고점은 하락 — 상승 동력 약화.
            **롱 온리에서 진입 근거로 쓰지 않는다** (모듈 docstring).
    """

    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass(frozen=True, slots=True)
class Divergence:
    """다이버전스 1건 (spec §6.1).

    Attributes:
        kind: 종류.
        first_index: 앞선 스윙의 봉 번호.
        second_index: 뒤따르는 스윙의 봉 번호.
        first_ts: 앞선 스윙 시각 (UTC).
        second_ts: 뒤따르는 스윙 시각 (UTC).
        first_price: 앞선 스윙 가격 (꼬리 끝).
        second_price: 뒤따르는 스윙 가격 (꼬리 끝).
        first_rsi: 앞선 스윙 봉의 RSI.
        second_rsi: 뒤따르는 스윙 봉의 RSI.

    Note:
        두 스윙의 좌표를 **둘 다** 남긴다. "다이버전스가 있다"만으로는 근거 요약
        (spec §5.3)도 차트 주석(§4.13)도 만들 수 없다.
    """

    kind: DivergenceKind
    first_index: int
    second_index: int
    first_ts: datetime
    second_ts: datetime
    first_price: Decimal
    second_price: Decimal
    first_rsi: float
    second_rsi: float


def rsi(close: Sequence[Decimal], period: int = STANDARD_PERIOD) -> list[float | None]:
    """RSI — Wilder 평활 (spec §6.1).

    Args:
        close: 종가 열.
        period: 기간. 기본값 14 는 spec §6.1 지정값이다.

    Returns:
        입력과 **같은 길이**의 결과. 첫 값은 index `period` 에 나온다 — 변화량이
        직전 종가를 쓰므로 `delta[0]` 이 없고, ATR 과 같은 이유다.

    Raises:
        SeriesError: `period` 가 1 미만인 경우.

    Note:
        평균 하락분이 0 이면 RSI 는 **100** 이다 (0으로 나누지 않는다). 무한 상승
        구간에서 실제로 발생하며, 이때 `None` 을 반환하면 "계산 불가"와 혼동된다.
    """
    require_period(period)
    gains: list[Decimal | None] = [None] * len(close)
    losses: list[Decimal | None] = [None] * len(close)
    for position in range(1, len(close)):
        delta = close[position] - close[position - 1]
        gains[position] = delta if delta > 0 else Decimal(0)
        losses[position] = -delta if delta < 0 else Decimal(0)

    average_gain = wilder_average(gains, period)
    average_loss = wilder_average(losses, period)

    result: list[float | None] = [None] * len(close)
    with fixed_context():
        for position in range(len(close)):
            up, down = average_gain[position], average_loss[position]
            if up is None or down is None:
                continue
            if down == 0:
                result[position] = 100.0
                continue
            strength = up / down
            result[position] = float(_HUNDRED - _HUNDRED / (Decimal(1) + strength))
    return result


def find_divergences(
    swings: Sequence[SwingPoint],
    rsi_values: Sequence[float | None],
) -> list[Divergence]:
    """가격 스윙과 RSI 를 대조해 다이버전스를 찾는다 (spec §6.1).

    Args:
        swings: **`structures.swing.prior_swings()` 결과** — 교대 정리된 전저점·전고점 열
            이어야 한다 (모듈 docstring). `find_pivots()` 결과를 넘기면 같은 봉우리의
            여러 점이 각각 신저점으로 세어진다.
        rsi_values: RSI 시리즈. 인덱스가 스윙의 `index` 와 같은 좌표계여야 한다.

    Returns:
        `second_index` 오름차순 다이버전스 목록.

    Raises:
        SeriesError: 스윙의 `index` 가 RSI 시리즈 범위를 벗어난 경우 — 좌표계가
            어긋났다는 뜻이며 조용히 넘기면 엉뚱한 봉의 RSI 를 비교한다.

    Note:
        **연속한 같은 종류의 스윙 쌍**만 본다. 교대 정리된 열에서 로우와 로우 사이에는
        하이가 하나 있으므로, 로우만 걸러낸 뒤 인접 쌍을 비교한다.

        RSI 가 `None` 인 스윙은 건너뛴다 — 워밍업 구간의 스윙은 비교 대상이 없다.
    """
    for swing in swings:
        if not 0 <= swing.index < len(rsi_values):
            raise SeriesError(
                f"스윙 index {swing.index} 가 RSI 시리즈 길이({len(rsi_values)}) 밖이다 — "
                f"좌표계가 어긋났다"
            )

    found: list[Divergence] = []
    for kind, wanted in (
        (DivergenceKind.BULLISH, SwingKind.LOW),
        (DivergenceKind.BEARISH, SwingKind.HIGH),
    ):
        same_kind = [
            swing
            for swing in swings
            if swing.kind is wanted and rsi_values[swing.index] is not None
        ]
        for first, second in pairwise(same_kind):
            first_rsi, second_rsi = rsi_values[first.index], rsi_values[second.index]
            if first_rsi is None or second_rsi is None:
                continue
            if kind is DivergenceKind.BULLISH:
                diverging = second.price < first.price and second_rsi > first_rsi
            else:
                diverging = second.price > first.price and second_rsi < first_rsi
            if not diverging:
                continue
            found.append(
                Divergence(
                    kind=kind,
                    first_index=first.index,
                    second_index=second.index,
                    first_ts=first.ts,
                    second_ts=second.ts,
                    first_price=first.price,
                    second_price=second.price,
                    first_rsi=first_rsi,
                    second_rsi=second_rsi,
                )
            )
    found.sort(key=lambda item: (item.second_index, item.kind))
    return found
