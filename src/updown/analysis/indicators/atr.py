"""ATR(14) — 손절폭 산정의 표준 (P1-2-3 · spec §6.1, §4.6).

## 이 값은 곧 주문 가격이 된다

spec §6.1: `stop = entry - k*ATR` (k 는 버킷별 1.5~3.0). 즉 ATR 은 **가격과 직접
연산**되고 그 결과가 호가단위 라운딩(§12.2)을 거쳐 실제 손절 주문가가 된다.
그래서 `Decimal` 이며 float 를 거치지 않는다 (`series` 모듈 docstring).

**ATR 이 손절가를 정하는 것은 아니다.** §6.1 은 "전저점 기반 손절과 병행해 **더 보수적인
쪽 선택**"이라고 못박는다. 최종 확정은 RiskManager 다 (절대 규칙 #4).

## True Range 는 첫 봉에서 정의되지 않는다

TR 은 직전 종가를 쓰므로 `tr[0]` 은 `None` 이다. 그 결과 ATR(14) 의 첫 값은
**index 14** 에 나온다 (index 1..14 의 TR 14개가 필요하다). 13이 아니라 14다 —
한 칸 차이가 손절폭을 어긋나게 하므로 테스트로 못박는다.
"""

from collections.abc import Sequence
from decimal import Decimal

from updown.analysis.indicators.series import (
    SeriesError,
    require_period,
    wilder_average,
)

STANDARD_PERIOD = 14
"""spec §6.1 이 지정한 ATR 기간. Wilder 원문 값이며 조정하지 않는다 (spec §5.6.2)."""


def true_range(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
) -> list[Decimal | None]:
    """True Range 시리즈.

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.

    Returns:
        입력과 **같은 길이**의 결과. `[0]` 은 직전 종가가 없어 `None` 이다.

    Raises:
        SeriesError: 세 열의 길이가 다른 경우.

    Note:
        `TR = max(high-low, |high-prev_close|, |low-prev_close|)` 다. 갭을 포함하기
        위해 직전 종가와의 거리를 함께 보는 것이 정의의 핵심이다 — 갭 오픈(§7)이
        실재하는 시장에서 `high-low` 만 쓰면 변동성을 과소평가한다.
    """
    if not (len(high) == len(low) == len(close)):
        raise SeriesError(f"고가·저가·종가 길이가 다르다: {len(high)} / {len(low)} / {len(close)}")
    result: list[Decimal | None] = [None] * len(close)
    for position in range(1, len(close)):
        previous_close = close[position - 1]
        result[position] = max(
            high[position] - low[position],
            abs(high[position] - previous_close),
            abs(low[position] - previous_close),
        )
    return result


def atr(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    period: int = STANDARD_PERIOD,
) -> list[Decimal | None]:
    """Average True Range — Wilder 평활 (spec §6.1).

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: 기간. 기본값 14 는 spec §6.1 지정값이다.

    Returns:
        입력과 **같은 길이**의 결과. 첫 값은 index `period` 에 나온다 (모듈 docstring).

    Raises:
        SeriesError: 길이가 다르거나 `period` 가 1 미만인 경우.
    """
    require_period(period)
    return wilder_average(true_range(high, low, close), period)
