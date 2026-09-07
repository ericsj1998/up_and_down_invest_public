"""스토캐스틱 — E-1 이 15m 에서 **◎** 로 놓은 지표 (T153).

    %K = (종가 - 최근 n봉 최저) / (최근 n봉 최고 - 최근 n봉 최저) x 100
    %D = %K 의 이동평균

## ⚠️ 느린(slow) 스토캐스틱이 기본이다

원문의 빠른(fast) %K 는 잡음이 심해 실무에서 거의 안 쓴다. 여기서는 **평활한 %K**
(= fast %D)를 `k` 로, 그것을 다시 평활한 것을 `d` 로 낸다 — 흔히 말하는
14-3-3 이 이 구성이다.

⚠️ 이 구별을 안 하면 같은 "스토캐스틱 교차" 가 구현마다 다른 시점에 난다.

## ⚠️ 분모가 0 인 봉이 실제로 있다

가격이 n봉 내내 한 점에 멈추면 최고 = 최저다. 1분봉 저유동 구간에서 실제로 생긴다.
그때는 `None` 이다 — 50 으로 채우면 없던 중립 신호가 생긴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.series import require_period
from updown.common.numeric import fixed_context

PERIOD = 14
SMOOTH_K = 3
SMOOTH_D = 3
"""표준 14-3-3."""


@dataclass(frozen=True, slots=True)
class StochasticSeries:
    """스토캐스틱 — 입력과 **같은 길이**.

    Attributes:
        k: 평활한 %K (0~100).
        d: %K 를 다시 평활한 %D.
    """

    k: list[Decimal | None]
    d: list[Decimal | None]

    def __len__(self) -> int:
        """봉 수."""
        return len(self.k)


def stochastic(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    *,
    period: int = PERIOD,
    smooth_k: int = SMOOTH_K,
    smooth_d: int = SMOOTH_D,
) -> StochasticSeries:
    """느린 스토캐스틱.

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: 최고·최저를 보는 봉 수.
        smooth_k: %K 평활 기간.
        smooth_d: %D 평활 기간.

    Returns:
        두 시리즈. 길이는 입력과 같다.

    Raises:
        SeriesError: 기간이 1 미만인 경우.
        ValueError: 열 길이가 다른 경우.

    Note:
        ⚠️ 분모가 0 인 봉(가격이 멈춘 구간)은 `None` 이다. 50 으로 채우면 없던 중립
        신호가 생기고, 저유동 구간에 그 봉이 몰려 있어 특정 시간대만 오염된다.
    """
    require_period(period)
    require_period(smooth_k)
    require_period(smooth_d)
    if not len(high) == len(low) == len(close):
        raise ValueError("고가·저가·종가 길이가 다르다")

    size = len(close)
    raw: list[Decimal | None] = [None] * size
    with fixed_context():
        for end in range(period - 1, size):
            start = end - period + 1
            top = max(high[start : end + 1])
            bottom = min(low[start : end + 1])
            span = top - bottom
            if span != 0:
                raw[end] = (close[end] - bottom) / span * 100

    k = _smooth(raw, smooth_k)
    d = _smooth(k, smooth_d)
    return StochasticSeries(k=k, d=d)


def _smooth(values: Sequence[Decimal | None], period: int) -> list[Decimal | None]:
    """결측을 건너뛰는 단순이동평균.

    Note:
        ⚠️ 창 안에 결측이 하나라도 있으면 결과도 결측이다. 있는 값만으로 평균을 내면
        **더 짧은 창**의 평균이 되고, 그것은 다른 지표다.
    """
    result: list[Decimal | None] = [None] * len(values)
    with fixed_context():
        for end in range(period - 1, len(values)):
            window = values[end - period + 1 : end + 1]
            if any(one is None for one in window):
                continue
            result[end] = sum((one for one in window if one is not None), start=Decimal(0)) / period
    return result


def williams(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    *,
    period: int = PERIOD,
) -> list[Decimal | None]:
    """Williams %R — 스토캐스틱 %K 의 **거울**이다 (OSC-11).

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: 창 길이.

    Returns:
        -100~0 시리즈. 워밍업과 분모 0 인 봉은 None.

    Note:
        ⭐ %R = %K - 100 이다. 같은 창의 같은 계산이므로 **다시 만들지 않고**
        평활 없는 %K 를 그대로 옮긴다 — 두 벌이 되면 언젠가 갈린다.

        ⚠️ 그래서 스토캐스틱과 **거의 같은 정보**다. 격자에 둘 다 넣으면 시행
        횟수만 늘어난다 — 그래도 넣는 이유는 문서(OSC-04/11)가 갈라 뒀기
        때문이고, 겹치는지는 유효 신호 수가 답한다.
    """
    raw = stochastic(high, low, close, period=period, smooth_k=1, smooth_d=1).k
    return [None if one is None else one - 100 for one in raw]
