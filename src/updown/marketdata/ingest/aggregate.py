"""하위 봉을 상위 시간축으로 합성한다 (P1 §1-0i 측정 · P1-8).

## 왜 합성인가 — 새 수집 없이 15m·4h 를 본다

적재된 것은 5m/1h/1d 다 (D-15). 15m(3봉)·4h(48봉)는 5m 의 **정확한 배수**이므로 경계
정렬만 맞추면 **손실 없이** 합성된다. 근사가 아니다 — 시가는 첫 봉, 종가는 마지막 봉,
고·저는 구간 극값, 거래량은 합이며 표준 OHLCV 집계 그대로다.

## 합성이 옳다는 것을 실측으로 확인했다

1h 을 **DB 원본**과 **5m 합성** 양쪽으로 돌렸을 때 ATR 뿐 아니라 **탐지 결과까지 완전히
일치**했다 (A1 5/2, A2 16/10 — Phase01 §1-0i). 15m·4h 합성값을 믿을 근거가 그 대조다.

## 불완전 그룹을 버리지도, 숨기지도 않는다

5m 봉이 모자란 그룹(수집 경계·결측)이 생긴다. **버리면** 계열에 구멍이 생겨 ATR 이 더
크게 왜곡되고, **조용히 포함하면** 왜곡이 안 보인다. 그래서 포함하되 개수를 함께 돌려준다
(절대 규칙 #8). 실측에서 35,415개 중 8~9개(0.02%)로 무해했다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.marketdata.ingest.timeframes import interval

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""버킷 정렬 기준점.

거래소가 상위 봉을 **UTC 자정 기준**으로 끊으므로 여기서도 그 기준을 쓴다. 로컬 시각으로
끊으면 같은 데이터에서 다른 봉이 나오고 결정론(원칙 P1)이 깨진다.
"""


class AggregationError(ValueError):
    """합성할 수 없다.

    Note:
        상위 시간축이 하위의 정확한 배수가 아니면 합성이 **근사**가 된다. 근사를 조용히
        허용하면 그 오차가 ATR·손절가로 번지므로 경계에서 막는다.
    """


@dataclass(frozen=True, slots=True)
class Aggregation:
    """합성 결과와 그 품질.

    Attributes:
        candles: 합성된 상위 봉 (`ts` 오름차순).
        incomplete: 하위 봉이 모자란 그룹 수. **조용히 넘기면 ATR 이 낮게 나온다.**
        expected: 완전한 그룹 하나에 들어갈 하위 봉 수.
    """

    candles: tuple[Candle, ...]
    incomplete: int
    expected: int

    @property
    def incomplete_ratio(self) -> Decimal:
        """불완전 그룹 비율 — 리포트에 그대로 싣는 값이다."""
        if not self.candles:
            return Decimal(0)
        return Decimal(self.incomplete) / Decimal(len(self.candles))


def bucket_start(ts: datetime, span: timedelta) -> datetime:
    """`ts` 가 속한 상위 봉의 시작 시각 — **UTC 자정 기준 정렬**.

    Args:
        ts: 하위 봉의 시작 시각 (UTC).
        span: 상위 봉의 길이.

    Returns:
        경계에 맞춘 시작 시각.
    """
    return _EPOCH + ((ts - _EPOCH) // span) * span


@dataclass(frozen=True, slots=True)
class CandleRow:
    """시간축 라벨이 없는 OHLCV 한 줄.

    Attributes:
        ts: 봉 시작 시각 (UTC).
        open: 시가.
        high: 고가.
        low: 저가.
        close: 종가.
        volume: 거래량.

    Note:
        **왜 `Candle` 이 아닌가**: 토스는 `1m` 만 주는데 우리 `Timeframe` 에는 `1m` 이 없다
        (D-8: `{5m,15m,1h,4h,1d}`). 1m 을 도메인 enum 에 넣으면 DB enum 마이그레이션까지
        번지는데, 1m 은 **분석·저장 대상이 아니라 5m 을 만들기 위한 중간 산물**일 뿐이다.

        그래서 중간 단계에서는 시간축 라벨 없는 행으로 다루고, **목표 시간축의 `Candle` 로
        곧바로** 승격한다.
    """

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def merge_rows(
    rows: Sequence[CandleRow],
    timeframe: Timeframe,
    instrument: Instrument,
    *,
    source_interval: timedelta,
) -> Aggregation:
    """시간축 라벨 없는 행들을 목표 시간축 캔들로 묶는다.

    Args:
        rows: `ts` 오름차순 행들.
        timeframe: 만들 시간축.
        instrument: 대상 종목.
        source_interval: 원본 행의 간격 (토스 분봉이면 1분).

    Returns:
        합성 결과.

    Raises:
        AggregationError: 입력이 비었거나 목표가 원본의 정확한 배수가 아닌 경우.

    Note:
        `aggregate()` 와 **같은 규칙**을 쓴다 — 시가=첫 봉, 종가=마지막 봉, 고·저=구간
        극값, 거래량=합. 규칙이 두 곳에 있으면 한쪽만 고쳐지므로 여기 하나에 둔다.

        ⚠️ **주식은 장이 닫히는 구간이 있다.** 그 구간에는 행 자체가 없으므로 버킷도 생기지
        않는다. 장 마감 직전처럼 행이 모자란 버킷은 `incomplete` 로 센다 — 버리면 마지막
        봉이 사라지고, 조용히 포함하면 그 봉이 짧다는 사실이 안 보인다.
    """
    if not rows:
        raise AggregationError("빈 행은 합성할 수 없다 — 무엇을 묶을지 알 수 없다")
    span = interval(timeframe)
    if span < source_interval or span % source_interval != timedelta(0):
        raise AggregationError(
            f"{source_interval} → {timeframe.value} 는 정확한 배수가 아니다 — "
            "합성이 근사가 되면 오차가 ATR·손절가로 번진다"
        )
    expected = span // source_interval

    groups: dict[datetime, list[CandleRow]] = {}
    for row in rows:
        groups.setdefault(bucket_start(row.ts, span), []).append(row)

    merged: list[Candle] = []
    incomplete = 0
    for start in sorted(groups):
        members = groups[start]
        if len(members) != expected:
            incomplete += 1
        merged.append(
            Candle(
                instrument=instrument,
                timeframe=timeframe,
                ts=start,
                open=members[0].open,
                high=max(member.high for member in members),
                low=min(member.low for member in members),
                close=members[-1].close,
                volume=sum((member.volume for member in members), Decimal(0)),
            )
        )
    return Aggregation(candles=tuple(merged), incomplete=incomplete, expected=expected)


def aggregate(base: Sequence[Candle], timeframe: Timeframe) -> Aggregation:
    """하위 봉을 상위 시간축으로 묶는다.

    Args:
        base: 하위 봉 (`ts` 오름차순). 전부 같은 시간축이어야 한다.
        timeframe: 만들 시간축.

    Returns:
        합성 결과. **불완전 그룹도 포함**하고 개수를 함께 돌려준다.

    Raises:
        AggregationError: 입력이 비었거나, 상위 시간축이 하위의 정확한 배수가 아닌 경우.

    Note:
        배수 검증을 하는 이유: 5m → 15m 은 정확히 3배지만 5m → 1d 처럼 큰 배수도,
        1h → 15m 처럼 **역방향**도 실수로 들어올 수 있다. 후자는 조용히 원본을 그대로
        돌려주게 되어 "합성했다"고 믿게 만든다.
    """
    if not base:
        raise AggregationError("빈 캔들은 합성할 수 없다 — 무엇을 묶을지 알 수 없다")

    source = base[0].timeframe
    span = interval(timeframe)
    step = interval(source)
    if span <= step or span % step != timedelta(0):
        raise AggregationError(
            f"{source.value} → {timeframe.value} 는 정확한 배수가 아니다 "
            f"({span} / {step}) — 합성이 근사가 되면 오차가 ATR·손절가로 번진다"
        )
    expected = span // step

    groups: dict[datetime, list[Candle]] = {}
    for candle in base:
        groups.setdefault(bucket_start(candle.ts, span), []).append(candle)

    merged: list[Candle] = []
    incomplete = 0
    for start in sorted(groups):
        members = groups[start]
        if len(members) != expected:
            incomplete += 1
        merged.append(
            Candle(
                instrument=members[0].instrument,
                timeframe=timeframe,
                ts=start,
                open=members[0].open,
                high=max(member.high for member in members),
                low=min(member.low for member in members),
                close=members[-1].close,
                volume=sum((member.volume for member in members), Decimal(0)),
            )
        )
    return Aggregation(candles=tuple(merged), incomplete=incomplete, expected=expected)
