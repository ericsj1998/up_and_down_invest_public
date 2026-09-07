"""켈트너 채널 · 도너치안 채널 — BND-05/06 (T153).

## 두 채널은 **다른 것을 잰다**

    켈트너    EMA ± ATR x k      → 변동성 대비 이탈 (볼린저의 ATR 판)
    도너치안  최근 N봉 최고/최저  → 신고가·신저가 갱신 (돌파 그 자체)

⚠️ 볼린저와 켈트너를 같은 신호로 묶지 않는다. 볼린저는 **표준편차**(종가 분산)이고
켈트너는 **ATR**(봉 폭)이다 — 갭이 잦은 구간에서 둘이 크게 갈린다.

⭐ 사실 그 차이가 스퀴즈 판정의 고전적 정의이기도 하다 (볼밴이 켈트너 **안**으로
들어가면 압축). 지금은 각각 재고, 둘을 엮는 것은 조합 단계(Stage 3)의 일이다.

## ⚠️ 도너치안은 **직전 N봉**이지 현재 봉을 포함하지 않는다

현재 봉을 창에 넣으면 신고가가 자기 자신 때문에 신고가가 되어 **항상 참**이다.
흔한 구현 실수이고, 넣으면 신호가 매 봉 발생한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.ma import ema
from updown.analysis.indicators.series import require_period
from updown.common.numeric import fixed_context

KELTNER_PERIOD = 20
KELTNER_MULTIPLE = Decimal(2)
DONCHIAN_PERIOD = 20
"""표준값. 손잡이 경쟁은 별도 축이다 (절대 규칙 #12)."""


@dataclass(frozen=True, slots=True)
class Channel:
    """상·중·하단 — 입력과 **같은 길이**.

    Attributes:
        middle: 중심선. 도너치안은 상하단의 가운데다.
        upper: 상단.
        lower: 하단.
    """

    middle: list[Decimal | None]
    upper: list[Decimal | None]
    lower: list[Decimal | None]

    def __len__(self) -> int:
        """봉 수."""
        return len(self.middle)


def keltner(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    *,
    period: int = KELTNER_PERIOD,
    multiple: Decimal = KELTNER_MULTIPLE,
) -> Channel:
    """켈트너 채널 — EMA ± ATR x 배수.

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: EMA·ATR 기간.
        multiple: ATR 배수.

    Returns:
        채널.

    Raises:
        SeriesError: 기간이 1 미만.
        ValueError: 배수가 0 이하이거나 열 길이가 다른 경우.

    Note:
        ⚠️ ATR 은 `atr()` 을 그대로 쓴다 — 그 함수가 True Range 의 첫 봉 처리를
        문서화해 두었고(정답지 대조 시험이 있다), 여기서 다시 만들면 두 벌이 된다.
    """
    require_period(period)
    if multiple <= 0:
        raise ValueError(f"배수는 0 보다 커야 한다: {multiple}")
    if not len(high) == len(low) == len(close):
        raise ValueError("고가·저가·종가 길이가 다르다")

    centre = ema(close, period)
    width = atr(high, low, close, period)
    upper: list[Decimal | None] = [None] * len(close)
    lower: list[Decimal | None] = [None] * len(close)
    with fixed_context():
        for index, (mid, span) in enumerate(zip(centre, width, strict=True)):
            if mid is None or span is None:
                continue
            upper[index] = mid + span * multiple
            lower[index] = mid - span * multiple
    return Channel(middle=centre, upper=upper, lower=lower)


def donchian(
    high: Sequence[Decimal], low: Sequence[Decimal], *, period: int = DONCHIAN_PERIOD
) -> Channel:
    """도너치안 채널 — **직전** N봉의 최고·최저.

    Args:
        high: 고가 열.
        low: 저가 열.
        period: 창 길이.

    Returns:
        채널.

    Raises:
        SeriesError: 기간이 1 미만.
        ValueError: 열 길이가 다른 경우.

    Note:
        🔴 **현재 봉을 창에 넣지 않는다.** 넣으면 신고가가 자기 자신 때문에
        신고가가 되어 조건이 항상 참이고, 신호가 매 봉 발생한다. 창은
        `[index-period, index-1]` 이다.
    """
    require_period(period)
    if len(high) != len(low):
        raise ValueError("고가·저가 길이가 다르다")

    size = len(high)
    upper: list[Decimal | None] = [None] * size
    lower: list[Decimal | None] = [None] * size
    middle: list[Decimal | None] = [None] * size
    with fixed_context():
        for index in range(period, size):
            top = max(high[index - period : index])
            bottom = min(low[index - period : index])
            upper[index] = top
            lower[index] = bottom
            middle[index] = (top + bottom) / 2
    return Channel(middle=middle, upper=upper, lower=lower)
