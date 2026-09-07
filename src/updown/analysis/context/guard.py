"""미래 참조(lookahead) 차단 — as-of 경계를 **타입으로** 강제한다 (P1-3-5 · spec §4.11, §4.2).

## 왜 필터링만으로는 부족한가

빌더가 `as_of` 이후 캔들을 그냥 빼고 넘기면 플러그인은 짧아진 리스트를 받는다. 그러면
잘못된 접근이 **예외가 아니라 조용한 오답**이 된다 — `candles[-1]` 은 언제나 뭔가를
돌려주므로 "마지막 봉"이 진짜 as-of 시점의 마지막인지 알 수 없다.

더 큰 문제는 **백테스트**다. 봉마다 리스트를 새로 잘라 넘기면 1년치 10만봉에서 O(n²) 이
된다. 그래서 전체 데이터를 한 번 올려두고 **경계만 옮기는** 방식이 필요하고, 그 순간
"뒤에 미래 데이터가 실제로 존재하는" 상태가 된다. 필터링은 그 구조를 아예 못 쓴다.

→ `AsOfSequence` 는 전체를 들고 있으면서 **보이는 범위를 넘는 접근에 예외를 던진다.**
효율(재슬라이스 없음)과 안전(조용한 통과 없음)을 동시에 얻는다.

## `IndexError` 가 아니라 `LookaheadError` 다

경계를 넘는 접근을 `IndexError` 로 처리하면 "리스트 끝을 지났다"와 **"미래를 보려 했다"**를
구분할 수 없다. 후자는 전략의 성과를 조용히 부풀리는 심각한 결함이고, 백테스트가 실거래보다
좋게 나오는 대표 원인이다 (D1-4 를 보수적으로 잡은 것과 같은 이유).

## 마감된 봉만 보인다

as-of 시점에 **진행 중인 봉은 종가가 없다.** 그 봉을 보이게 하면 아직 결정되지 않은
종가로 판단하는 셈이고, spec §4.2 의 "탐지는 봉마감 기준"을 어긴다.

`visible_upto()` 가 `ts + interval <= as_of` 인 봉까지만 센다. P0-8 이 미완성 봉을 저장하지
않는 것과 같은 규칙을 **읽는 쪽에서도** 지키는 것이다.
"""

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import overload

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval


class LookaheadError(RuntimeError):
    """as-of 시점 이후의 데이터에 접근했다.

    Note:
        `IndexError` 와 구별하는 것이 핵심이다 (모듈 docstring). 이 예외가 나오면
        **전략 코드에 미래 참조가 있다**는 뜻이며, 조용히 넘기면 백테스트 성과가
        부풀려진다.
    """


def visible_upto(
    candles: Sequence[Candle],
    as_of: datetime,
    timeframe: Timeframe,
) -> int:
    """as-of 시점에 **마감이 끝난** 봉의 개수.

    Args:
        candles: `ts` 오름차순 캔들.
        as_of: 기준 시각 (UTC aware).
        timeframe: 봉 간격 판정용 시간축.

    Returns:
        보여도 되는 봉 수. 0 일 수 있다 — as-of 가 데이터 시작보다 앞이면 정상이다.

    Note:
        경계는 `ts + interval <= as_of` 다. `ts <= as_of` 로 하면 **진행 중인 봉이
        포함**되고, 그 봉의 종가는 아직 결정되지 않았다 (spec §4.2).
    """
    step = interval(timeframe)
    visible = 0
    for candle in candles:
        if candle.ts + step > as_of:
            break
        visible += 1
    return visible


class AsOfSequence(Sequence[Candle]):
    """as-of 경계를 강제하는 읽기 전용 캔들 뷰.

    Attributes:
        as_of: 기준 시각 (UTC).

    Note:
        원본 리스트를 **복사하지 않는다.** 백테스트가 봉마다 새 뷰를 만들어도 비용이
        상수이며, 그것이 이 클래스가 존재하는 이유다 (모듈 docstring).

        `len()` 은 **보이는 개수**를 돌려준다. 그래서 `for candle in view` 나
        `view[-1]` 같은 평범한 파이썬 관용구가 자동으로 안전해진다 — 플러그인이
        경계를 의식하지 않아도 된다.
    """

    __slots__ = ("_source", "_visible", "as_of")

    def __init__(self, source: Sequence[Candle], visible: int, as_of: datetime) -> None:
        """뷰를 만든다.

        Args:
            source: 전체 캔들 (as-of 이후를 포함할 수 있다).
            visible: 보여도 되는 개수.
            as_of: 기준 시각 (UTC aware).

        Raises:
            ValueError: `visible` 이 음수이거나 원본 길이를 넘는 경우.
        """
        if not 0 <= visible <= len(source):
            raise ValueError(
                f"visible({visible})이 0~{len(source)} 범위를 벗어났다 — 경계 계산이 틀렸다"
            )
        self._source = source
        self._visible = visible
        self.as_of = as_of

    @classmethod
    def until(
        cls,
        source: Sequence[Candle],
        as_of: datetime,
        timeframe: Timeframe,
    ) -> "AsOfSequence":
        """마감 경계를 스스로 계산해 뷰를 만든다.

        Args:
            source: 전체 캔들.
            as_of: 기준 시각 (UTC aware).
            timeframe: 시간축.

        Returns:
            마감된 봉만 보이는 뷰.
        """
        return cls(source, visible_upto(source, as_of, timeframe), as_of)

    def __len__(self) -> int:
        """보이는 봉 수."""
        return self._visible

    @overload
    def __getitem__(self, index: int) -> Candle: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Candle]: ...

    def __getitem__(self, index: int | slice) -> Candle | Sequence[Candle]:
        """봉 하나 또는 구간을 읽는다 — 경계를 넘으면 `LookaheadError`.

        Args:
            index: 봉 번호 또는 슬라이스. 음수는 **보이는 범위** 기준이다.

        Returns:
            캔들 또는 캔들 리스트.

        Raises:
            LookaheadError: 보이는 범위를 넘어선 접근.

        Note:
            음수 인덱스를 원본 길이가 아니라 **보이는 길이** 기준으로 푸는 것이
            중요하다. 원본 기준으로 풀면 `view[-1]` 이 미래의 마지막 봉을 준다.
        """
        if isinstance(index, slice):
            start, stop, step = index.indices(self._visible)
            del start, stop, step  # indices() 가 이미 보이는 범위로 잘라 준다
            return list(self._source[: self._visible])[index]
        position = index if index >= 0 else self._visible + index
        if not 0 <= position < self._visible:
            if 0 <= position < len(self._source):
                raise LookaheadError(
                    f"as_of={self.as_of:%Y-%m-%d %H:%M} 기준으로 보이지 않는 봉에 접근했다 "
                    f"(index {index}, 보이는 봉 {self._visible}개). "
                    f"미래 참조는 백테스트 성과를 조용히 부풀린다 (spec §4.11)"
                )
            raise LookaheadError(
                f"index {index} 가 범위를 벗어났다 (보이는 봉 {self._visible}개, "
                f"원본 {len(self._source)}개)"
            )
        return self._source[position]

    def __iter__(self) -> Iterator[Candle]:
        """보이는 봉만 순회한다.

        Note:
            **직접 구현해야 한다.** `Sequence` 의 기본 `__iter__` 는 `IndexError` 를
            종료 신호로 쓰는데 이 클래스는 경계에서 `LookaheadError` 를 던지므로,
            상속에 맡기면 순회가 예외로 끝난다 — P1-3 개발 중 실제로 그렇게 깨졌고
            `list(view)` 조차 동작하지 않았다.
        """
        for position in range(self._visible):
            yield self._source[position]

    def index(
        self,
        value: Candle,
        start: int = 0,
        stop: int | None = None,
    ) -> int:
        """보이는 범위에서만 찾는다.

        Args:
            value: 찾을 캔들.
            start: 시작 위치.
            stop: 끝 위치 (배제). None 이면 보이는 끝까지.

        Returns:
            위치.

        Raises:
            ValueError: 보이는 범위에 없는 경우.

        Note:
            `Sequence.index` 기본 구현도 `IndexError` 에 의존하므로 함께 덮는다
            (`__iter__` 와 같은 이유).
        """
        limit = self._visible if stop is None else min(stop, self._visible)
        for position in range(max(start, 0), limit):
            if self._source[position] == value:
                return position
        raise ValueError(f"{value!r} 는 보이는 범위(0~{self._visible - 1})에 없다")

    def count(self, value: Candle) -> int:
        """보이는 범위에서의 개수.

        Args:
            value: 셀 봉.

        Returns:
            창 안에서 같은 봉의 수. `Sequence.count` 규약 — 창 밖(미래)은 세지 않는다.
        """
        return sum(1 for candle in self if candle == value)

    def full_length(self) -> int:
        """원본 전체 길이 — **진단용**이다.

        Returns:
            as-of 이후를 포함한 전체 봉 수.

        Note:
            값 자체를 판단에 쓰면 미래 정보를 쓰는 것이다. 테스트와 로그에만 쓴다.
        """
        return len(self._source)
