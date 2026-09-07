"""시장 구조 판정 — HH/HL vs LH/LL (P1-4-3 · spec §4.16 판정 입력 2번).

## 왜 `prior_swings()` 를 쓰는가

"고점·저점 **동반** 상승"은 전고점·전저점 개념이다. `structures.swing.prior_swings()`
(zigzag)가 그 정의를 이미 갖고 있다 (spec §6.4). 구조물 작도용 `find_pivots()` 를 쓰면
같은 봉우리의 여러 점이 각각 "전고점"으로 세어져 HH/LH 판정이 무의미해진다.

P1-1 에서 스윙 출력을 두 갈래로 나눈 것이 여기서 또 값을 한다
(`docs/rules/structure_rules.md` §2).

## 판정에는 스윙 4개가 필요하다

HH 는 고점 2개, HL 은 저점 2개를 요구한다. 교대 열에서 그것은 **최소 4개**다. 부족하면
`UNKNOWN` 이며 `MIXED` 와 구별한다 — 전자는 "볼 데이터가 없다", 후자는 "봤는데 섞여
있다"이고 대응이 다르다 (절대 규칙 #8).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.common.domain.trend import StructurePattern

MIN_SWINGS_FOR_STRUCTURE = 4
"""구조 판정에 필요한 최소 스윙 수 — 고점 2개 + 저점 2개 (모듈 docstring)."""


@dataclass(frozen=True, slots=True)
class StructureReading:
    """시장 구조 판정 결과.

    Attributes:
        pattern: 구조 패턴.
        last_high: 가장 최근 스윙 하이. 없으면 None.
        previous_high: 그 직전 스윙 하이. 없으면 None.
        last_low: 가장 최근 스윙 로우. 없으면 None.
        previous_low: 그 직전 스윙 로우. 없으면 None.

    Note:
        좌표를 함께 돌려주는 이유: CHoCH·BOS 판정이 "직전 LH 가격"을 필요로 하고,
        구조 판정과 전환 판정이 **같은 스윙**을 봐야 한다. 각자 다시 찾으면 어긋난다.
    """

    pattern: StructurePattern
    last_high: SwingPoint | None
    previous_high: SwingPoint | None
    last_low: SwingPoint | None
    previous_low: SwingPoint | None

    @property
    def higher_high(self) -> bool | None:
        """최근 고점이 직전 고점보다 높은가 (HH). 판정 불가면 None."""
        if self.last_high is None or self.previous_high is None:
            return None
        return self.last_high.price > self.previous_high.price

    @property
    def higher_low(self) -> bool | None:
        """최근 저점이 직전 저점보다 높은가 (HL). 판정 불가면 None."""
        if self.last_low is None or self.previous_low is None:
            return None
        return self.last_low.price > self.previous_low.price


def _last_two(
    swings: Sequence[SwingPoint], kind: SwingKind
) -> tuple[SwingPoint | None, SwingPoint | None]:
    """해당 종류의 마지막 두 스윙 — `(최근, 직전)`."""
    matching = [swing for swing in swings if swing.kind is kind]
    last = matching[-1] if matching else None
    previous = matching[-2] if len(matching) >= 2 else None
    return last, previous


def read_structure(swings: Sequence[SwingPoint]) -> StructureReading:
    """스윙 열에서 시장 구조를 판정한다 (spec §4.16).

    Args:
        swings: **`prior_swings()` 결과** — 교대 정리된 전고점·전저점 열이어야 한다
            (모듈 docstring).

    Returns:
        구조 판정. 스윙이 4개 미만이면 `UNKNOWN` 이다.

    Note:
        HH **와** HL 이 함께여야 `HIGHER` 다. 고점만 오르고 저점이 안 오르는 것은 상승
        구조가 아니라 확산(broadening)이며, 그것을 상승으로 읽으면 고점 추격이 된다.
    """
    last_high, previous_high = _last_two(swings, SwingKind.HIGH)
    last_low, previous_low = _last_two(swings, SwingKind.LOW)
    reading = StructureReading(
        pattern=StructurePattern.UNKNOWN,
        last_high=last_high,
        previous_high=previous_high,
        last_low=last_low,
        previous_low=previous_low,
    )
    if len(swings) < MIN_SWINGS_FOR_STRUCTURE:
        return reading
    hh, hl = reading.higher_high, reading.higher_low
    if hh is None or hl is None:
        return reading
    if hh and hl:
        pattern = StructurePattern.HIGHER
    elif not hh and not hl:
        pattern = StructurePattern.LOWER
    else:
        pattern = StructurePattern.MIXED
    return StructureReading(
        pattern=pattern,
        last_high=last_high,
        previous_high=previous_high,
        last_low=last_low,
        previous_low=previous_low,
    )


def ma200_slope(series: Sequence[Decimal | None], lookback: int) -> Decimal | None:
    """200 SMA 의 봉당 기울기 (spec §4.16 판정 입력 1번).

    Args:
        series: 200 SMA 시리즈.
        lookback: 기울기를 잴 봉 수.

    Returns:
        `(현재 - lookback 전) / lookback`. 양쪽 중 하나라도 None 이면 None.

    Note:
        절대 금액이라 종목 간 비교에 쓸 수 없다. **부호와 자기 종목 내 추이**만 쓴다 —
        비율로 정규화하면 그럴듯해 보이지만 그 값의 의미를 아무도 정의하지 않았다.
    """
    if lookback < 1 or len(series) <= lookback:
        return None
    current = series[-1]
    earlier = series[-1 - lookback]
    if current is None or earlier is None:
        return None
    return (current - earlier) / Decimal(lookback)
