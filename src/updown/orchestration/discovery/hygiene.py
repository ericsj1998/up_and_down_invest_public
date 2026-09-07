"""**데이터 위생** — 조용히 채우지 않는다 (T152 §3 · 계획서 §0-1).

## 🔴 네 가지를 다르게 다룬다

    봉 누락       **비운다.** 앞 값으로 채우면 없던 가격이 생기고 그 가격에 신호가 걸린다
    중복 타임스탬프  하나만 남기고 **몇 개를 지웠는지 남긴다**
    거래소 점검     구간을 **표시**한다. 그 안의 매매는 체결이 불가능했다
    극단 급락      ⛔ **지우지 않는다** — 청산 판정의 진짜 재료다. 표시만 한다

마지막 줄이 제일 중요하다. 급락을 지우면 백테스트가 예뻐지고 **하드 제약(청산)이
과소평가**된다 — 그리고 그 과소평가는 실계좌에서만 드러난다.

## ⚠️ 문턱은 가정이다 — 그래서 **분포를 같이 낸다**

*"연속 결측 몇 봉부터 점검인가"* 와 *"한 봉에 몇 %부터 급락인가"* 에 정답이 없다.
그래서 이 모듈은 문턱으로 자른 목록과 **길이별 분포**를 함께 낸다. 분포를 보고
문턱이 말이 되는지 확인한 다음 고정한다 (CLAUDE.md 관측 규약 §1-0s: *"새 규칙이
값을 만들면 그 값의 분포를 리포트에 싣는다"*).

## ⛔ 고쳐 주지 않는다

이 모듈은 **보고만** 한다. 정렬도 보간도 하지 않는다. 데이터가 깨졌다는 것은 받는
쪽이 알아야 할 사실이고, 조용히 고치면 무엇이 깨졌는지 모르는 채로 결과가 나온다
(절대 규칙 #8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval_seconds

__all__ = ["Gap", "Report", "Spike", "inspect", "keep_indices"]

HALT_BARS = 10
"""이만큼 **연속으로** 비면 거래소 점검 후보로 본다.

⚠️ **가정이다.** 상위 8종 무기한에서 1~2분이 비는 것은 유동성이지만, 10분 연속으로
체결이 없는 것은 유동성으로 설명되지 않는다. `Report.gap_sizes` 분포를 보고 이 값이
말이 되는지 확인한 다음 고정한다.
"""

SPIKE_PCT = 5.0
"""한 봉에서 이만큼(%) 움직이면 급락·급등으로 표시한다.

⛔ **표시만 한다. 지우지 않는다.** 이 봉들이 청산 판정의 재료다 — 지우면 하드 제약이
과소평가되고, 그 오류는 실계좌에서만 드러난다.
"""


@dataclass(frozen=True, slots=True)
class Gap:
    """빠진 구간.

    Attributes:
        after_ms: 마지막으로 있던 봉의 시각.
        before_ms: 다음으로 있는 봉의 시각.
        missing: 그 사이에 없는 봉 수.
    """

    after_ms: int
    before_ms: int
    missing: int


@dataclass(frozen=True, slots=True)
class Spike:
    """한 봉에서의 극단 움직임.

    Attributes:
        index: 봉 번호.
        ts: 봉 시각.
        move_pct: 시가 대비 종가 변화(%). 부호가 있다.
        range_pct: 고가-저가 폭(%). 청산 판정이 보는 값이다.
    """

    index: int
    ts: int
    move_pct: float
    range_pct: float


@dataclass(frozen=True, slots=True)
class Report:
    """한 종목·한 축의 위생 보고서.

    Attributes:
        bars: 실제 봉 수.
        expected: 처음~끝을 빈틈없이 채웠다면 나왔을 봉 수.
        first_ms: 첫 봉 시각.
        last_ms: 마지막 봉 시각.
        duplicates: 중복 타임스탬프 수.
        backwards: 시각이 뒤로 간 횟수 — 0 이 아니면 파일이 깨졌다.
        gaps: 빠진 구간들.
        halts: 그중 `halt_bars` 이상 — 점검 후보.
        spikes: 극단 움직임들.
        gap_sizes: 결측 길이 → 몇 번. **문턱을 검증하는 재료다.**

    Note:
        ⚠️ `coverage` 가 1.0 이 아니면 그 축의 성과에는 *"구간의 몇 %만 봤다"* 가
        따라다녀야 한다. 결측이 무작위가 아니면(점검은 대개 변동성 구간이다) 성과가
        조용히 낙관 쪽으로 기운다.
    """

    bars: int
    expected: int
    first_ms: int
    last_ms: int
    duplicates: int
    backwards: int
    gaps: tuple[Gap, ...]
    halts: tuple[Gap, ...]
    spikes: tuple[Spike, ...]
    gap_sizes: dict[int, int]

    @property
    def missing(self) -> int:
        """빠진 봉 총수."""
        return sum(one.missing for one in self.gaps)

    @property
    def coverage(self) -> float:
        """있어야 할 봉 중 실제로 있는 비율."""
        return self.bars / self.expected if self.expected else 0.0

    @property
    def span_days(self) -> float:
        """구간 길이(일)."""
        return (self.last_ms - self.first_ms) / 86_400_000

    def clean(self) -> bool:
        """손 볼 것이 없나.

        Returns:
            중복·역행·결측이 전부 없으면 True.
        """
        return not self.duplicates and not self.backwards and not self.gaps


def keep_indices(ts: Sequence[int]) -> list[int]:
    """중복을 뺀 인덱스 — **첫 번째 것만** 남긴다.

    Args:
        ts: 봉 시각들 (밀리초).

    Returns:
        남길 인덱스, 오름차순.

    Note:
        🔴 인덱스를 돌려주는 이유는 **모든 열에 같은 것을 적용**하기 위해서다.
        시각만 걸러 내고 가격 열은 그대로 두면 그 순간 시각과 가격이 어긋난다.

        ⚠️ 정렬해 주지 않는다. 역행이 있으면 그 사실이 `Report.backwards` 에 남고,
        여기서는 그냥 지나간다 — 조용히 고치면 파일이 깨진 것을 모른다.
    """
    seen: set[int] = set()
    kept: list[int] = []
    for index, moment in enumerate(ts):
        if moment in seen:
            continue
        seen.add(moment)
        kept.append(index)
    return kept


def inspect(
    ts: Sequence[int],
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    *,
    timeframe: Timeframe,
    halt_bars: int = HALT_BARS,
    spike_pct: float = SPIKE_PCT,
) -> Report:
    """봉 열을 훑어 보고서를 낸다 — **고치지는 않는다**.

    Args:
        ts: 봉 시각들 (밀리초).
        open_: 시가 열.
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        timeframe: 이 열의 시간축. 기대 간격을 여기서 얻는다.
        halt_bars: 점검 후보로 볼 연속 결측 봉 수.
        spike_pct: 급락·급등으로 볼 한 봉 변화율(%).

    Returns:
        보고서.

    Raises:
        ValueError: 열 길이가 서로 다른 경우.

    Note:
        ⚠️ 간격을 데이터에서 추측하지 않고 `timeframe` 에서 얻는다. 추측하면
        결측이 많은 종목에서 **최빈 간격이 2배**로 나오고, 그러면 결측이 0 으로
        보고된다 — 가장 나쁜 종류의 조용한 실패다.
    """
    if not (len(ts) == len(open_) == len(high) == len(low) == len(close)):
        raise ValueError("열 길이가 다르다")
    if not ts:
        return Report(
            bars=0,
            expected=0,
            first_ms=0,
            last_ms=0,
            duplicates=0,
            backwards=0,
            gaps=(),
            halts=(),
            spikes=(),
            gap_sizes={},
        )

    step = interval_seconds(timeframe) * 1000
    duplicates = 0
    backwards = 0
    gaps: list[Gap] = []
    sizes: dict[int, int] = {}
    for before, after in pairwise(ts):
        moved = after - before
        if moved == 0:
            duplicates += 1
        elif moved < 0:
            backwards += 1
        elif moved > step:
            missing = moved // step - 1
            gaps.append(Gap(after_ms=before, before_ms=after, missing=missing))
            sizes[missing] = sizes.get(missing, 0) + 1

    spikes: list[Spike] = []
    for index, (o, h, low_, c) in enumerate(zip(open_, high, low, close, strict=True)):
        if o <= 0:
            continue
        move = (c - o) / o * 100
        width = (h - low_) / o * 100
        if abs(move) >= spike_pct or width >= spike_pct:
            spikes.append(Spike(index=index, ts=ts[index], move_pct=move, range_pct=width))

    expected = (ts[-1] - ts[0]) // step + 1 if len(ts) > 1 else 1
    return Report(
        bars=len(ts),
        expected=expected,
        first_ms=ts[0],
        last_ms=ts[-1],
        duplicates=duplicates,
        backwards=backwards,
        gaps=tuple(gaps),
        halts=tuple(one for one in gaps if one.missing >= halt_bars),
        spikes=tuple(spikes),
        gap_sizes=sizes,
    )
