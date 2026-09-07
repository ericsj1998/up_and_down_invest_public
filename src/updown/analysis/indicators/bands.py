"""볼린저 밴드 — **압축→확장**을 재기 위한 지표 (T153 · spec §6.2 권장 항목).

## 🔴 오늘 유일하게 살아남은 신호가 이것이었다

2026-08-30, 1분봉 36판 중 **압축→확장만** Gross 양수였다 (+0.077%, 역방향 -0.097%
로 대칭). 나머지는 전부 0 근처였다. 그런데 그 측정은 임시 코드로 했고 **코드에는
볼린저가 없었다** — 그래서 여기 제대로 만든다.

    폭 = (상단 - 하단) / 중심 x 100

⭐ 폭을 **중심으로 나눈다.** 절대폭으로 재면 BTC 와 DOGE 를 한 표에 못 올린다.

## ⚠️ 표준편차는 **모집단**(n) 이지 표본(n-1)이 아니다

볼린저 원문이 모집단 편차를 쓴다. n-1 로 재면 짧은 기간에서 밴드가 약간 넓어지고,
그러면 *"압축"* 판정 문턱이 조용히 달라진다.

## ⚠️ Decimal 로 제곱근을 낸다

`float` 를 거치면 20봉 합에서 유효숫자가 깎인다. `Decimal.sqrt()` 를 고정 컨텍스트
안에서 쓴다 (`ma.ema` 와 같은 논거).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.series import require_period, simple_average
from updown.common.numeric import fixed_context

PERIOD = 20
MULTIPLE = Decimal(2)
"""표준값 (Bollinger 원문). 기간·배수 경쟁은 별도 축으로 한다 (절대 규칙 #12)."""


@dataclass(frozen=True, slots=True)
class BandSeries:
    """볼린저 밴드 — 전부 입력과 **같은 길이**다.

    Attributes:
        middle: 중심선 (SMA).
        upper: 상단.
        lower: 하단.
        width: 폭(%) = (상단 - 하단) / 중심 x 100. **종목 간 비교가 되는 값이다.**
        position: 밴드 내 위치. 0 이면 하단, 1 이면 상단. 밖으로 나가면 범위를 넘는다.

    Note:
        ⭐ `position` 이 %B 다. 상단 돌파(>1)·하단 이탈(<0)을 한 값으로 표현하므로
        신호를 쓰는 쪽이 상단·하단을 따로 비교하지 않아도 된다.
    """

    middle: list[Decimal | None]
    upper: list[Decimal | None]
    lower: list[Decimal | None]
    width: list[Decimal | None]
    position: list[Decimal | None]

    def __len__(self) -> int:
        """봉 수."""
        return len(self.middle)


def bollinger(
    close: Sequence[Decimal], *, period: int = PERIOD, multiple: Decimal = MULTIPLE
) -> BandSeries:
    """볼린저 밴드.

    Args:
        close: 종가 열.
        period: 기간.
        multiple: 표준편차 배수.

    Returns:
        밴드 시리즈. 길이는 `close` 와 같다.

    Raises:
        SeriesError: 기간이 1 미만인 경우.
        ValueError: 배수가 0 이하인 경우.

    Note:
        ⚠️ **모집단 표준편차**(n 으로 나눔)다. 표본 편차(n-1)로 재면 밴드가 넓어지고
        압축 판정 문턱이 조용히 달라진다.

        ⚠️ 중심선이 0 이면 폭을 못 낸다 — 가격이 0 인 경우이므로 실제로는 안 생기지만
        `None` 으로 두고 넘어간다. 0 으로 나누어 죽는 것보다 낫다.
    """
    require_period(period)
    if multiple <= 0:
        raise ValueError(f"배수는 0 보다 커야 한다: {multiple}")

    size = len(close)
    middle: list[Decimal | None] = [None] * size
    upper: list[Decimal | None] = [None] * size
    lower: list[Decimal | None] = [None] * size
    width: list[Decimal | None] = [None] * size
    position: list[Decimal | None] = [None] * size

    with fixed_context():
        count = Decimal(period)
        for end in range(period - 1, size):
            start = end - period + 1
            mean = simple_average(close, start, period)
            variance = (
                sum(
                    ((close[index] - mean) ** 2 for index in range(start, end + 1)),
                    start=Decimal(0),
                )
                / count
            )
            deviation = variance.sqrt()
            top = mean + deviation * multiple
            bottom = mean - deviation * multiple
            middle[end] = mean
            upper[end] = top
            lower[end] = bottom
            if mean != 0:
                width[end] = (top - bottom) / mean * 100
            span = top - bottom
            # ⚠️ 편차가 0 이면 밴드가 한 점이라 위치를 못 낸다 (가격이 완전히 멈춘 구간).
            if span != 0:
                position[end] = (close[end] - bottom) / span

    return BandSeries(middle=middle, upper=upper, lower=lower, width=width, position=position)
