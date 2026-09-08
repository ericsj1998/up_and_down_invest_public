"""스윙 하이/로우 탐지 — **순수 함수** (P1-1-2 · spec §6.4, §4.3.1).

## 왜 공용 모듈인가

spec §4.3.1 이 요구한다: 플러그인마다 스윙을 따로 계산하면 **합류(confluence) 판정이
불가능**해진다. "오더블록과 추세선이 겹치는 자리"를 보려면 두 플러그인이 같은 스윙을
보고 있어야 한다.

## 꼬리 끝 기준 (전역 규칙)

스윙 하이의 가격은 `high`, 스윙 로우의 가격은 `low` 다 — **종가·몸통이 아니다**
(spec §6.5 "추세선은 꼬리 끝 기준으로 작도"). 이 규칙이 여기서 시작하기 때문에
추세선·채널의 앵커가 자동으로 꼬리 끝이 된다. 추세선 모듈이 따로 지킬 것이 없다.

## 두 가지 출력이 있고, 섞어 쓰면 안 된다 ⚠️

| 함수 | 출력 | 쓰는 곳 |
|------|------|---------|
| `find_pivots()` | N봉 극값 **전부** | **구조물 작도** — 추세선(§6.5)·박스 |
| `zigzag()` | 하이/로우 **교대**만 | **전저점·전고점**(§6.4)·되돌림 레벨 |

zigzag 는 **반전 필터**다. 사이에 하이가 없는 로우들을 "같은 레그"로 보고 가장 극단적인
하나만 남긴다. 그런데 **오르는 저점들이 바로 상승 지지선의 재료**다 — 추세선을 zigzag
결과로 그으면 저점 6개가 1개로 줄어 선이 아예 안 나온다.

실데이터에서는 하이·로우가 촘촘히 교대해 감소가 미미하지만, 구조적으로 틀린 입력을
쓰는 것이다. 그래서 두 함수를 분리하고 **합친 편의 함수를 두지 않는다** — 있으면
어느 쪽인지 모르고 쓰게 된다.

## 결측 봉을 건너뛰지 않는다 (P0-8 인계 사항)

Phase 0 에서 **거래소 점검으로 인한 결측 구간이 연 45회 규모**임을 실측했다
(`docs/rules/candle_integrity_rules.md` §7). 결측이 있으면 배열상 인접한 두 봉이 시간상
인접하지 않는다 — 6시간 구멍을 사이에 둔 두 봉으로 "5봉 프랙탈"을 판정하면 그 스윙은
의미가 없다.

→ **연속 구간(segment)으로 쪼개서 각 구간 안에서만 극값을 찾는다.** 점검창을 가로지르는
스윙은 만들지 않는다. 이것이 조용히 잘못된 스윙을 만드는 것보다 정직하다.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.analysis.structures.params import SwingParams
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.domain.structure import Anchor
from updown.marketdata.ingest.timeframes import interval


class SwingKind(StrEnum):
    """스윙 포인트의 종류.

    Attributes:
        HIGH: 스윙 하이 — 가격은 `candle.high` (꼬리 끝).
        LOW: 스윙 로우 — 가격은 `candle.low` (꼬리 끝).
    """

    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """탐지된 스윙 포인트 (spec §6.4).

    Attributes:
        index: 입력 캔들 배열 내 위치. 추세선의 기울기 계산과 접점 판정이 이 값을
            x 좌표로 쓴다.
        ts: 봉 시작 시각 (UTC).
        price: **꼬리 끝** 가격 — HIGH 면 `high`, LOW 면 `low`.
        kind: 종류.

    Note:
        `index` 를 담는 이유는 시간이 아니라 **봉 번호**가 추세선의 x축이기 때문이다.
        시간을 x축으로 쓰면 결측 구간에서 기울기가 왜곡된다 (없는 봉만큼 시간이
        흘렀으므로 선이 완만해진다).
    """

    index: int
    ts: datetime
    price: Decimal
    kind: SwingKind

    def to_anchor(self) -> Anchor:
        """추세선 앵커로 바꾼다.

        Returns:
            같은 시각·가격의 앵커 — 좌표는 그대로 꼬리 끝이다.
        """
        return Anchor(ts=self.ts, price=self.price)


def contiguous_segments(
    candles: Sequence[Candle],
    timeframe: Timeframe,
) -> list[tuple[int, int]]:
    """캔들을 시간상 연속인 구간들로 쪼갠다.

    Args:
        candles: `ts` 오름차순 캔들.
        timeframe: 봉 간격 판정용 시간축.

    Returns:
        `[start, end)` 인덱스 쌍 목록. 결측이 없으면 항목 1개다.

    Note:
        결측 구간을 경계로 삼는다 (모듈 docstring). 구간이 잘게 쪼개져 프랙탈 폭보다
        짧아지면 그 구간에서는 스윙이 나오지 않는다 — 그것이 맞는 결과다.
    """
    if not candles:
        return []
    step = interval(timeframe)
    segments: list[tuple[int, int]] = []
    start = 0
    for position in range(1, len(candles)):
        if candles[position].ts - candles[position - 1].ts != step:
            segments.append((start, position))
            start = position
    segments.append((start, len(candles)))
    return segments


def _is_pivot_high(prices: Sequence[Decimal], at: int, left: int, right: int) -> bool:
    """좌측은 **엄격히**, 우측은 같아도 인정한다.

    Args:
        prices: 고가 열. 캔들이 아니라 값 리스트를 받는다 (아래 Note).
        at: 판정할 위치.
        left: 좌측 비교 봉 수.
        right: 우측 비교 봉 수.

    Returns:
        스윙 하이면 True.

    Note:
        비대칭이 의도다. 같은 고가가 연속(평평한 고점)일 때 양쪽 모두 엄격하면 스윙이
        아예 안 잡히고, 양쪽 모두 느슨하면 평평한 봉 전부가 스윙이 된다. 좌엄격/우느슨은
        **평평한 구간의 첫 봉**을 고르며, 이 규칙은 결정론적이다 (원칙 P1).

        캔들이 아니라 값 열을 받는 이유는 `candle.high` 반복 조회를 줄이는 것이다.
        다만 실측하면 그 효과는 4% 수준이었다 — 10만봉 규모의 실제 병목은 `zigzag()`
        의 O(n^2) 였다 (`docs/rules/indicator_rules.md` §7.1). 병목을 추측하지 않고 측정한
        결과를 남긴다.
    """
    price = prices[at]
    return all(prices[index] < price for index in range(at - left, at)) and all(
        prices[index] <= price for index in range(at + 1, at + right + 1)
    )


def _is_pivot_low(prices: Sequence[Decimal], at: int, left: int, right: int) -> bool:
    """좌측은 엄격히, 우측은 같아도 인정한다 (`_is_pivot_high` 와 대칭).

    Args:
        prices: 저가 열.
        at: 판정할 위치.
        left: 좌측 비교 봉 수.
        right: 우측 비교 봉 수.

    Returns:
        스윙 로우면 True.
    """
    price = prices[at]
    return all(prices[index] > price for index in range(at - left, at)) and all(
        prices[index] >= price for index in range(at + 1, at + right + 1)
    )


def find_pivots(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    params: SwingParams | None = None,
) -> list[SwingPoint]:
    """N봉 프랙탈 극값을 모두 찾는다 (spec §6.4 "N봉 기준 극값").

    Args:
        candles: `ts` 오름차순 캔들.
        timeframe: 결측 판정용 시간축.
        params: 좌우 비교 봉 수. None 이면 표준값(2/2).

    Returns:
        `index` 오름차순 스윙 포인트. 한 봉이 하이·로우를 동시에 만족할 수 있다
        (양쪽으로 삐죽한 봉).

    Note:
        **추세선·박스 작도는 이 결과를 쓴다** (모듈 docstring 표). `zigzag()` 를 거치면
        오르는 저점들이 하나로 줄어 지지선이 사라진다.

        마지막 `right_bars` 개 봉은 **원리적으로 판정 불가**다. 우측 확인 봉이 아직
        없기 때문이다. 미래를 당겨쓰지 않으므로 여기서 스윙이 나오지 않는 것이 맞다
        (lookahead 금지 — spec §4.11).
    """
    settings = params or SwingParams()
    left, right = settings.left_bars, settings.right_bars
    # 값 열을 한 번만 뽑는다 — 봉마다 `candle.high` 를 다시 조회하면 10만봉 규모에서
    # 그 속성 접근이 계산의 대부분을 차지한다 (`_is_pivot_high` Note).
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    stamps = [candle.ts for candle in candles]

    pivots: list[SwingPoint] = []
    for start, end in contiguous_segments(candles, timeframe):
        for at in range(start + left, end - right):
            if _is_pivot_high(highs, at, left, right):
                pivots.append(SwingPoint(at, stamps[at], highs[at], SwingKind.HIGH))
            if _is_pivot_low(lows, at, left, right):
                pivots.append(SwingPoint(at, stamps[at], lows[at], SwingKind.LOW))
    pivots.sort(key=lambda p: (p.index, p.kind))
    return pivots


def _more_extreme(candidate: SwingPoint, incumbent: SwingPoint) -> bool:
    """같은 종류의 두 스윙 중 candidate 가 더 극단적인가."""
    if candidate.kind is SwingKind.HIGH:
        return candidate.price > incumbent.price
    return candidate.price < incumbent.price


def zigzag(pivots: Sequence[SwingPoint]) -> list[SwingPoint]:
    """하이·로우가 교대하도록 정리한다 — **전저점·전고점 판정용** (spec §6.4).

    Args:
        pivots: `find_pivots()` 결과.

    Returns:
        하이/로우가 번갈아 나오는 스윙 열. 연속된 같은 종류는 **더 극단적인 것만**
        남는다.

    Note:
        ⚠️ **추세선·박스 작도에 이 결과를 쓰지 않는다** (모듈 docstring 표). 반전
        필터이므로 추세의 재료인 "오르는 저점들"을 하나로 뭉갠다.

        교대를 강제하는 이유는 전고점·전저점의 정의가 그것이기 때문이다 (spec §6.4).
        하이가 둘 연속이면 사이에 로우가 없다는 뜻이고, 그 둘은 같은 봉우리의 두 점이다.

        한 봉이 하이·로우를 동시에 만족할 때는 **직전 스윙과 교대되는 쪽을 먼저**
        본다 — 인덱스만으로 정렬하면 같은 봉에서 어느 쪽을 택하느냐가 임의가 되고,
        그 임의성이 결정론을 깬다.

        같은 봉의 묶음을 찾을 때 **남은 리스트를 슬라이스하지 않는다.** 슬라이스는
        매번 사본을 만들어 전체가 O(n²) 이 된다 — 10만봉(스윙 약 2.5만개)에서 5.5초를
        먹었고, 픽스처(240~288봉)에서는 드러나지 않았다 (P1-2 에서 실측).
    """
    last: list[SwingPoint] = []
    for _, kept in zigzag_events(pivots, right_bars=0):
        last = kept
    return last


def zigzag_events(
    pivots: Sequence[SwingPoint], *, right_bars: int
) -> Iterator[tuple[int, list[SwingPoint]]]:
    """교대 정리를 **한 걸음씩** 내준다 — 각 단계가 그 시점에 알 수 있는 열이다.

    Args:
        pivots: `find_pivots()` 결과.
        right_bars: 프랙탈이 극값을 확정하는 데 쓴 우측 봉 수.

    Yields:
        `(알게 되는 봉, 그때까지의 교대 열)`.

    Note:
        🔴 **왜 필요한가.** `zigzag()` 결과를 통째로 쓰면 미래 참조가 된다 —
        교대 정리는 같은 방향 구간에서 더 극단적인 점이 나중에 나오면 앞의 점을
        **교체**하므로, 전 구간 열의 어떤 원소도 *"그 시점에 그 값이었다"* 를
        보장하지 않는다.

        실측(2026-08-31 · BTC 1h): 스윙 429개 중 3개가 굳는 데 6·7·9봉이 걸렸다.
        그 0.7% 가 다이버전스 4종 전부에서 미래 참조로 잡혔고, **상수 지연을 키우는
        것으로도 다음 스윙을 기다리는 것으로도 안 풀렸다** (다음 스윙 자신이 또
        교체되기 때문이다).

        ⭐ 접기는 원래 **왼쪽에서 오른쪽으로 가는 폴드**다. 그러니 각 단계를 그대로
        내주면 그것이 곧 인과적인 열이다 — 새 알고리즘이 아니라 이미 있던 것을
        한 걸음씩 보여 주는 것뿐이다.

        ⚠️ 내주는 열은 **살아 있다** (다음 단계에서 바뀐다). 보관하려면 복사한다 —
        전부 복사하면 O(n²) 이므로, 소비자는 꼬리 몇 개만 보는 것이 맞다.

        🔴 `zigzag()` 가 이 생성기를 **쓴다.** 둘을 따로 구현하면 언젠가 갈리고,
        그때 갈린 쪽이 미래를 보는 쪽이 된다.
    """
    kept: list[SwingPoint] = []
    for group in _same_index_groups(pivots, 0, len(pivots)):
        _fold_into(kept, group)
        yield group[0].index + right_bars, kept


def _same_index_groups(
    pivots: Sequence[SwingPoint],
    start: int,
    stop: int,
) -> Iterator[list[SwingPoint]]:
    """같은 봉에 찍힌 피벗들을 한 묶음으로 끊어 낸다.

    Args:
        pivots: `(index, kind)` 오름차순 피벗.
        start: 시작 위치.
        stop: 끝 위치(미포함).

    Yields:
        같은 `index` 를 가진 피벗 묶음. 대개 1개다.
    """
    position = start
    while position < stop:
        end = position
        while end < stop and pivots[end].index == pivots[position].index:
            end += 1
        yield list(pivots[position:end])
        position = end


def _fold_into(kept: list[SwingPoint], group: list[SwingPoint]) -> None:
    """교대 규칙에 따라 한 묶음을 접어 넣는다 — **`kept` 를 제자리에서 고친다**.

    Args:
        kept: 지금까지 접힌 스윙 열.
        group: 같은 봉의 피벗 묶음.

    Note:
        `zigzag()` 와 `RollingZigzag` 가 **이 함수 하나를 공유**한다. 규칙을 두 벌로
        두면 한쪽만 고쳐져 배치 결과와 증분 결과가 조용히 갈라진다 (절대 규칙 #5).
    """
    if len(group) > 1 and kept:
        group.sort(key=lambda p: (p.kind == kept[-1].kind, p.kind))
    for pivot in group:
        if not kept or kept[-1].kind is not pivot.kind:
            kept.append(pivot)
        elif _more_extreme(pivot, kept[-1]):
            kept[-1] = pivot


class RollingZigzag:
    """피벗이 **뒤로만 붙을 때** `zigzag()` 를 이어서 접는다.

    같은 앞토막을 봉마다 처음부터 다시 접으면 O(n²) 이 된다. 접기는 좌→우 폴드라
    상태(`kept`)만 들고 있으면 이어붙일 수 있고, 결과는 `zigzag()` 와 **같다**
    (`tests/test_pivot_prefix.py` 가 잠근다).

    Note:
        같은 봉의 피벗들은 확인 시점(`index + right_bars`)이 같아 **항상 함께** 들어온다
        — 묶음이 쪼개지지 않으므로 교대 정렬 규칙이 배치와 동일하게 적용된다.

        ⚠️ `upto()` 는 **사본**을 돌려준다. 내부 리스트를 그대로 넘기면 다음 봉에서
        `kept[-1] = pivot` 이 이미 넘긴 결과를 소급 변경한다.
    """

    __slots__ = ("_fed", "_kept")

    def __init__(self) -> None:
        """빈 상태로 시작한다 — 캔들 열 하나마다 새로 만든다."""
        self._kept: list[SwingPoint] = []
        self._fed = 0

    def upto(self, pivots: Sequence[SwingPoint], count: int) -> list[SwingPoint]:
        """앞에서 `count` 개까지 접은 결과.

        Args:
            pivots: 전체 피벗. 호출 사이에 **앞부분이 바뀌지 않아야** 한다.
            count: 접어 넣을 개수. 직전 호출보다 작으면 안 된다.

        Returns:
            교대 정리된 스윙 열의 사본.

        Raises:
            ValueError: `count` 가 뒤로 물러난 경우. 창이 미끄러졌다는 뜻이고, 그때는
                이어붙이기가 성립하지 않으므로 조용히 틀린 답을 내지 않는다 (규칙 #8).
        """
        if count < self._fed:
            raise ValueError(f"피벗이 뒤로 물러났다: {self._fed} → {count}")
        for group in _same_index_groups(pivots, self._fed, count):
            _fold_into(self._kept, group)
        self._fed = count
        return list(self._kept)


def prior_swings(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    params: SwingParams | None = None,
) -> list[SwingPoint]:
    """전저점·전고점 열을 만든다 — 극값 탐색 후 교대 정리 (spec §6.4).

    Args:
        candles: `ts` 오름차순 캔들.
        timeframe: 시간축.
        params: 스윙 파라미터.

    Returns:
        교대 정리된 스윙 열.

    Note:
        이름이 `detect_swings` 가 아닌 이유: "스윙 탐지"라는 중립적 이름이면 추세선
        작도에도 이것을 넘기게 된다. 용도가 이름에 박혀 있어야 오용이 줄어든다.
        추세선·박스에는 `find_pivots()` 를 쓴다.
    """
    return zigzag(find_pivots(candles, timeframe, params))
