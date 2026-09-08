"""CCI (Commodity Channel Index) — OSC-11 (T153).

    전형가격 TP = (고 + 저 + 종) / 3
    CCI = (TP - SMA(TP, n)) / (0.015 x 평균절대편차)

## ⚠️ 0.015 는 마법수가 아니라 **정의의 일부**다

Lambert 원문 상수이고, 이것 덕분에 CCI 의 70~80% 가 -100~+100 안에 들어온다.
바꾸면 다른 지표가 되므로 손잡이로 두지 않는다.

## ⚠️ **평균절대편차**이지 표준편차가 아니다

흔한 구현 실수다. 표준편차로 만들면 값의 크기가 달라져 ±100 문턱이 다른 뜻이 된다 —
같은 이름의 다른 지표가 되고, 그 표는 남의 것과 비교가 안 된다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.series import require_period, simple_average
from updown.common.numeric import fixed_context

PERIOD = 20
FACTOR = Decimal("0.015")
"""Lambert 원문 상수. **정의의 일부**라 손잡이가 아니다."""


@dataclass(frozen=True, slots=True)
class CciSeries:
    """CCI 시리즈 — 입력과 같은 길이.

    Attributes:
        value: CCI. 워밍업은 `None`.
        typical: 전형가격. 다른 지표가 재사용할 수 있게 같이 낸다.
    """

    value: list[Decimal | None]
    typical: list[Decimal]

    def __len__(self) -> int:
        """봉 수."""
        return len(self.value)


def cci(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    *,
    period: int = PERIOD,
) -> CciSeries:
    """CCI.

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: 기간.

    Returns:
        시리즈.

    Raises:
        SeriesError: 기간이 1 미만.
        ValueError: 열 길이가 다른 경우.

    Note:
        ⚠️ 평균절대편차가 0 인 봉(가격이 창 내내 멈춤)은 `None` 이다. 0 으로 나누는
        대신 비워 두며, 0 으로 채우면 없던 중립 신호가 생긴다.
    """
    require_period(period)
    if not len(high) == len(low) == len(close):
        raise ValueError("고가·저가·종가 길이가 다르다")

    with fixed_context():
        three = Decimal(3)
        typical = [
            (top + bottom + last) / three
            for top, bottom, last in zip(high, low, close, strict=True)
        ]
        value: list[Decimal | None] = [None] * len(typical)
        count = Decimal(period)
        for end in range(period - 1, len(typical)):
            start = end - period + 1
            mean = simple_average(typical, start, period)
            deviation = (
                sum((abs(typical[i] - mean) for i in range(start, end + 1)), start=Decimal(0))
                / count
            )
            if deviation == 0:
                continue
            value[end] = (typical[end] - mean) / (FACTOR * deviation)
    return CciSeries(value=value, typical=typical)
