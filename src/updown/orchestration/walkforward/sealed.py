"""봉인 구간 — **커서 이후는 읽을 수 없다** (T13 ④).

## 🔴 화면에서 안 그리는 것으로는 부족하다

사용자 요구는 *"특정 시점부터 라이브라고 가정하고 그 이후 데이터를 봉인"* 이다. 이것을
UI 에서만 막으면, 계산 어딘가가 미래를 보고 있어도 **아무도 모른다.** 이번 프로젝트에서
같은 형태의 사고가 이미 두 번 났다:

```
점검기 재생   as-of 계획을 창 시작부터 굴려 존재하지도 않던 진입이 찍혔다
레벨 선택     접점을 창 전체로 세어 "나중에 강해질 레벨"을 미리 골랐다
```

⇒ 봉인은 **데이터를 주는 쪽**이 강제한다. 커서 이후를 달라고 하면 조용히 자르지 않고
`SealBreach` 를 던진다 (절대 규칙 #8). 조용히 자르면 호출부는 자기가 미래를 요구한 줄
모르고, 그 버그는 성과가 이상해질 때까지 숨는다.

## 되감기는 되고 미래로는 못 간다

사용자 확정 — *"과거로 되돌릴 시 일시정지 상태에서 분석·매매는 멈춤. 미래로는 갈 수
없음."* 그래서 커서는 **앞으로만** 움직이고(`advance`), 보기용 되감기는 커서를 건드리지
않는 별도 개념이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from updown.analysis.context.guard import AsOfSequence
from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from updown.common.domain.candle import Candle


class SealBreachError(RuntimeError):
    """봉인 구간을 읽으려 했다.

    Note:
        🔴 **조용히 자르지 않는 이유가 이 예외다.** 자르면 호출부는 자기가 미래를
        요구한 줄 모르고, 결과만 조금 달라진다 — 그런 버그는 성과가 이상해질 때까지
        숨는다.
    """


@dataclass(frozen=True, slots=True)
class Seal:
    """봉인 구간의 경계.

    Attributes:
        start: 걸어가기 시작 시각. 이 시점까지는 **이미 아는 과거**다.
        end: 봉인 끝. 여기를 넘어서는 걸어갈 수 없다.

    Note:
        ⚠️ `end` 는 보유 데이터의 끝을 넘을 수 없다 (T13 ④). 넘으면 마지막 구간이
        빈 봉으로 채워지고, 그것을 "매매 기회가 없었다"로 읽게 된다.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """경계가 말이 되는지 확인한다.

        Raises:
            ValueError: 시작이 끝보다 늦거나 같은 경우, 또는 naive datetime 인 경우.
        """
        for label, moment in (("start", self.start), ("end", self.end)):
            if moment.tzinfo is None:
                raise ValueError(f"{label} 이 naive 다 — 저장·비교는 UTC 로 한다 (§12.3)")
        if self.start >= self.end:
            raise ValueError(f"봉인 구간이 비었다 — start {self.start} >= end {self.end}")


class SealedFeed:
    """시간축별 전체 캔들을 들고, **커서까지만** 내준다.

    Note:
        🔴 커서 이후를 요구하면 `SealBreach` 다. 잘라서 주지 않는다.

        ⭐ 뷰는 `AsOfSequence` 라 `len()`·`[-1]`·순회가 자동으로 안전하다 — 분석
        코드가 경계를 의식하지 않아도 된다. 이미 있는 장치를 다시 만들지 않는다.
    """

    def __init__(self, source: Mapping[Timeframe, Sequence[Candle]], seal: Seal) -> None:
        """봉인된 급전을 만든다.

        Args:
            source: 시간축별 **전체** 캔들 (봉인 구간 포함).
            seal: 봉인 경계.

        Raises:
            ValueError: 어느 시간축이든 데이터가 봉인 끝에 못 미치는 경우.

        Note:
            🔴 **데이터가 모자라면 지금 터진다.** 걸어가다 끝에서 빈 봉을 만나면
            "매매 기회가 없었다"로 읽히는데, 사실은 데이터가 없었던 것이다 (T13 ④).
        """
        self._source = {frame: list(rows) for frame, rows in source.items()}
        self._seal = seal
        self._cursor = seal.start
        for frame, rows in self._source.items():
            if not rows:
                raise ValueError(f"{frame.value} 캔들이 비었다")
            last = rows[-1].ts
            if last + interval(frame) < seal.end:
                raise ValueError(
                    f"{frame.value} 데이터가 봉인 끝에 못 미친다 — "
                    f"마지막 {last.isoformat()} < 봉인 끝 {seal.end.isoformat()}. "
                    f"봉인 기간을 줄이거나 데이터를 더 적재한다"
                )

    @property
    def cursor(self) -> datetime:
        """지금 "현재"로 치는 시각."""
        return self._cursor

    @property
    def seal(self) -> Seal:
        """봉인 경계."""
        return self._seal

    @property
    def received_at(self) -> datetime:
        """봉인 급전에는 "지금" 이 없다 — 커서가 곧 수신 시각이다 (T15-2)."""
        return self._cursor

    @property
    def finished(self) -> bool:
        """봉인 끝까지 걸어갔는가."""
        return self._cursor >= self._seal.end

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """들고 있는 시간축들."""
        return tuple(self._source)

    def judged(self, frame: Timeframe, *, at: datetime | None = None) -> AsOfSequence:
        """그 시각까지 **닫힌** 봉만 보이는 뷰.

        Args:
            frame: 시간축.
            at: 기준 시각. 안 주면 현재 커서. 되감기 관찰에 쓴다.

        Returns:
            읽기 전용 뷰.

        Raises:
            SealBreach: `at` 이 커서보다 미래인 경우.
            KeyError: 안 들고 있는 시간축.

        Note:
            🔴 **커서보다 미래를 요구하면 터진다.** 되감기(`at` 이 과거)는 허용한다 —
            사용자 확정대로 보기만 하는 것이며, 분석·매매는 세션이 따로 멈춘다.
        """
        moment = self._cursor if at is None else at
        if moment > self._cursor:
            raise SealBreachError(
                f"봉인 위반 — {moment.isoformat()} 을 요구했지만 커서는 "
                f"{self._cursor.isoformat()} 다. 미래로는 갈 수 없다 (T13 ⑤)"
            )
        if frame not in self._source:
            raise KeyError(f"{frame.value} 는 이 세션에 없다 — 들고 있는 것: {self.timeframes}")
        return AsOfSequence.until(self._source[frame], moment, frame)

    def observed(self, frame: Timeframe) -> AsOfSequence:
        """**화면·감사용 보기** — 봉인 급전에서는 `judged` 와 같다 (T15-1).

        Args:
            frame: 시간축.

        Returns:
            커서까지 마감된 봉.

        Note:
            🔴 **같은 값인 것이 정직하다.** 봉인 구간에는 "지금" 이 없다 — 커서가 곧
            현재이고, 그 밖은 미래다. 여기서 더 많이 돌려주면 백테스트가 미래를 보게
            되고, 그것이 이 클래스가 존재하는 이유를 부순다 (절대 규칙 #5).

            ⚠️ 그래서 **이 메서드는 라이브를 위해 있다.** 상위(감사·화면)가 두 급전을
            구별하지 않고 `observed` 를 부를 수 있어야 하고, 그러려면 봉인 쪽에도
            같은 이름이 있어야 한다.
        """
        return self.judged(frame)

    def advance(self, frame: Timeframe) -> bool:
        """커서를 그 시간축 **한 봉**만큼 앞으로.

        Args:
            frame: 전진 단위가 되는 시간축. 보통 가장 작은 축이다.

        Returns:
            움직였으면 True. 봉인 끝에 닿았으면 False.

        Note:
            ⛔ **뒤로 가는 `advance` 는 없다.** 커서를 되돌리면 그 뒤 봉을 이미 본 채로
            다시 판정하게 되고, 그것은 미래 참조와 같다. 되감기는 `view(at=...)` 로
            **보기만** 한다.
        """
        if self.finished:
            return False
        self._cursor = min(self._cursor + interval(frame), self._seal.end)
        return True

    def progress(self) -> float:
        """걸어간 비율 0~1 — 화면 진행 막대용.

        Returns:
            봉인 구간 대비 커서 위치. 구간이 0 이면 0.
        """
        span = (self._seal.end - self._seal.start).total_seconds()
        done = (self._cursor - self._seal.start).total_seconds()
        return 0.0 if span <= 0 else min(1.0, max(0.0, done / span))
