"""MACD — 지금까지 **코드에 없던** 지표 (T153 · spec §6.2 권장 항목).

## 🔴 `Indicators.macd` 는 계속 `None` 이었다

`snapshot.py` 가 *"`macd`/`bb`/`vwap` 은 항상 None 이다 — spec §6.2 권장(Phase 2)
항목이다"* 라고 적어 두고 비워 뒀다. E섹션 TF 적합도표는 MACD 를 1H·4H 에서 **◎** 로
놓았는데, 그 칸을 재려면 지표가 있어야 한다.

## 정의는 표준값을 그대로 쓴다

    빠른 EMA 12 · 느린 EMA 26 · 시그널 EMA 9

절대 규칙 #12(권위가 아니라 성과가 판정한다)에 따르면 이 값도 후보일 뿐이지만,
**한 번에 한 축**이 원칙이다. 기간 경쟁은 지표가 선 다음에 별도로 한다.

## ⚠️ 시그널선은 **MACD 선의 EMA** 다 — 가격의 EMA 가 아니다

흔한 구현 실수이고, 틀리면 히스토그램의 부호가 다른 시점에 바뀐다.

## ⚠️ O(n²) 를 조심한다

2026-08-30 에 실제로 겪었다: 히스토그램을 만들면서 리스트 컴프리헨션 **안에서**
`ema(line, 9)` 를 불러 원소마다 EMA 전체를 다시 계산했고, 65분을 돌고도 안 끝났다.
시그널 EMA 는 루프 **밖에서** 한 번만 만든다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.ma import ema
from updown.analysis.indicators.series import require_period

FAST = 12
SLOW = 26
SIGNAL = 9
"""표준 기간 (Appel 원문). 기간 경쟁은 지표가 선 다음 별도 축으로 한다."""


@dataclass(frozen=True, slots=True)
class MacdSeries:
    """MACD 3종 시리즈 — 전부 입력과 **같은 길이**다.

    Attributes:
        line: MACD 선 = 빠른 EMA - 느린 EMA.
        signal: 시그널선 = MACD 선의 EMA.
        histogram: line - signal.

    Note:
        ⚠️ 워밍업 구간은 `None` 이다. 길이를 맞추는 이유는 인덱스가 봉 번호에 그대로
        대응해야 하기 때문이다 (`series` 모듈 계약 1번).
    """

    line: list[Decimal | None]
    signal: list[Decimal | None]
    histogram: list[Decimal | None]

    def __len__(self) -> int:
        """봉 수."""
        return len(self.line)


def macd(
    close: Sequence[Decimal],
    *,
    fast: int = FAST,
    slow: int = SLOW,
    signal: int = SIGNAL,
) -> MacdSeries:
    """MACD 선·시그널·히스토그램.

    Args:
        close: 종가 열.
        fast: 빠른 EMA 기간.
        slow: 느린 EMA 기간.
        signal: 시그널 EMA 기간.

    Returns:
        세 시리즈. 길이는 `close` 와 같다.

    Raises:
        SeriesError: 기간이 1 미만인 경우.
        ValueError: 빠른 기간이 느린 기간보다 짧지 않은 경우 — 그러면 부호가 통째로
            뒤집혀 모든 신호가 반대가 된다.

    Note:
        🔴 시그널선을 만들 때 **MACD 선에 결측이 있는 구간을 건너뛴다.** 워밍업의
        `None` 을 0 으로 채우면 시그널선이 0 쪽으로 끌려가 초반에 없는 교차가 생긴다.
    """
    require_period(fast)
    require_period(slow)
    require_period(signal)
    if fast >= slow:
        raise ValueError(f"빠른 기간이 느린 기간보다 짧아야 한다: {fast} >= {slow}")

    quick = ema(close, fast)
    slack = ema(close, slow)
    line: list[Decimal | None] = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(quick, slack, strict=True)
    ]

    # ⚠️ 시그널은 MACD 선의 EMA 다. 결측을 빼고 계산한 뒤 제자리에 돌려놓는다 —
    #    0 으로 채우면 초반이 0 쪽으로 끌려 없는 교차가 생긴다.
    start = next((index for index, one in enumerate(line) if one is not None), len(line))
    dense = [one for one in line[start:] if one is not None]
    smoothed = ema(dense, signal)

    signal_line: list[Decimal | None] = [None] * len(line)
    for offset, value in enumerate(smoothed):
        signal_line[start + offset] = value

    histogram: list[Decimal | None] = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(line, signal_line, strict=True)
    ]
    return MacdSeries(line=line, signal=signal_line, histogram=histogram)
