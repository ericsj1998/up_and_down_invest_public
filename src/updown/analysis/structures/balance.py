"""밸런스 · 임밸런스 구조화 — 추세를 마디로 쪼갠다 (사용자 매매 룰 · 2026-08-13).

## 무엇인가

가격은 **한쪽으로 몰아친 구간**과 **갇혀서 왔다갔다 한 구간**을 번갈아 간다:

```
임밸런스 ↗   밸런스 ~~~   임밸런스 ↗   밸런스 ~~~   임밸런스 ↗
(추세를 만든다)  (쉬어 간다)
```

사용자 정의: *"임밸런스는 추세를 만드는 주된 움직임, 밸런스는 특정 가격 포인트 안에
갇혀서 움직이지 못하는 구간."*

## 🔴 게이트는 **하나**다 — 몸통 갭

불균형의 계량형은 ICT/SMC 의 **FVG(Fair Value Gap)** 다: 연속 3봉에서 1번과 3번이
겹치지 않으면 그 사이는 **거래 없이 지나간 가격대**이고, 그것이 곧 불균형이다.
사용자 지정대로 **꼬리가 아니라 몸통**으로 본다 (`fvg.py` 는 꼬리 기준이라 다른 축이다).

⛔ **크기(ATR 배수)·거래량·체류 봉 수를 게이트로 쓰지 않는다.** 전부 속성으로만
기록한다. `order_block.py` 가 세운 규율과 같다:

> ③⑤는 가산이고 ①②④는 게이트다. 스펙이 "점수 가산"이라 쓴 것을 게이트로 바꾸면
> **탐지 개수가 파라미터에 좌우되고**, 그 파라미터를 성과로 조정하는 뒷문이 열린다.

실제로 오더블록에서 임계 하나(감쌈)가 봉의 **91%** 를 지웠고, 세 게이트의 교집합이
99.99% 를 걷어냈다 (§1-0z). 여기서 같은 실수를 반복하지 않으려고 게이트를 하나로
묶었고, **임계값이 아예 없는** 것을 골랐다.

속성이 게이트가 될지는 §4.14 한계 기여가 정한다 — 지금 정하면 그것이 곧 손잡이다.

## ⛔ 추세를 여기서 판정하지 않는다

`trend/service.py` 가 추세의 SSoT 다. 이 모듈은 **구조만** 낸다 — "이 마디가 추세와
같은 방향인가"는 그것을 읽는 쪽이 정한다. 여기서 판정하면 두 번째 추세 판정기가
생기고, 그때 "게이트가 본 추세"와 "구조가 본 추세"가 갈라진다 (§4.16).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from updown.analysis.structures.params import SwingParams
from updown.analysis.structures.swing import prior_swings
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.numeric import fixed_context


class LegKind(StrEnum):
    """마디의 종류.

    Attributes:
        IMBALANCE: 몸통 갭이 난 구간 — 한쪽으로 몰아쳤다.
        BALANCE: 임밸런스 사이 — 갇혀서 왔다갔다 했다.
    """

    IMBALANCE = "imbalance"
    BALANCE = "balance"


class LegDirection(StrEnum):
    """마디의 방향.

    Attributes:
        UP: 상승 임밸런스. `DOWN`: 하락 임밸런스.
        FLAT: 밸런스 — 방향이 없다는 것이 정의다.
    """

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class Leg:
    """구조 한 마디.

    Attributes:
        kind: 임밸런스인가 밸런스인가.
        direction: 방향. 밸런스는 `FLAT`.
        start: 시작 봉 번호 (포함). `end`: 끝 봉 번호 (포함).
        low: 구간의 **몸통** 최저. `high`: 구간의 **몸통** 최고.
        volume: 구간 누적 거래량 — 속성이지 게이트가 아니다.
        gaps: 이 마디 안에서 난 몸통 갭 수. 밸런스는 0 이다.
        displacement_atr: 순이동 / 시작 시점 ATR. ATR 을 안 주면 None.

    Note:
        🔴 경계 가격이 **몸통 기준**이다 (사용자 지정). 꼬리로 잡으면 스윕 한 번에
        구간이 늘어나 "갇혀 있었다"는 뜻이 흐려진다.

        `volume`·`gaps`·`displacement_atr` 은 **기록일 뿐** 탐지를 가르지 않는다
        (모듈 docstring).
    """

    kind: LegKind
    direction: LegDirection
    start: int
    end: int
    low: Decimal
    high: Decimal
    volume: Decimal
    gaps: int
    displacement_atr: Decimal | None

    @property
    def bars(self) -> int:
        """마디의 봉 수 — 밸런스의 **체류 기간**이 이 값이다."""
        return self.end - self.start + 1


def body_low(candle: Candle) -> Decimal:
    """몸통 하단 (시가·종가 중 낮은 쪽).

    Args:
        candle: 봉.

    Returns:
        `min(open, close)`. 꼬리를 뺀 몸통 기준 — 구조물 작도는 몸통으로 한다 (§6.3).
    """
    return min(candle.open, candle.close)


def body_high(candle: Candle) -> Decimal:
    """몸통 상단 (시가·종가 중 높은 쪽).

    Args:
        candle: 봉.

    Returns:
        `max(open, close)`.
    """
    return max(candle.open, candle.close)


def gap_at(candles: Sequence[Candle], index: int) -> LegDirection:
    """`index` 봉이 3봉 묶음의 **끝**일 때 몸통 갭이 났는가.

    Args:
        candles: `ts` 오름차순 캔들.
        index: 묶음의 마지막 봉 번호. 2 이상이어야 한다.

    Returns:
        갭 방향. 없으면 `FLAT`.

    Note:
        상승 갭은 `1번 몸통 상단 < 3번 몸통 하단` 이다 — 그 사이 가격대에서 **거래가
        일어나지 않았다**는 뜻이고, 그것이 불균형의 정의다.

        ⚠️ **등호를 넣지 않는다.** 딱 맞닿은 것은 갭이 아니라 연속이다.
    """
    if index < 2 or index >= len(candles):
        return LegDirection.FLAT
    first, third = candles[index - 2], candles[index]
    if body_high(first) < body_low(third):
        return LegDirection.UP
    if body_low(first) > body_high(third):
        return LegDirection.DOWN
    return LegDirection.FLAT


def _leg(
    candles: Sequence[Candle],
    kind: LegKind,
    direction: LegDirection,
    start: int,
    end: int,
    gaps: int,
    atr_series: Sequence[Decimal | None] | None,
) -> Leg:
    """구간 하나를 `Leg` 로 접는다.

    Args:
        candles: 전체 캔들.
        kind: 마디 종류.
        direction: 마디 방향.
        start: 시작 봉.
        end: 끝 봉 (포함).
        gaps: 갭 수.
        atr_series: 캔들과 길이가 같은 ATR 계열. 없으면 `displacement_atr` 이 None.

    Returns:
        마디.
    """
    window = candles[start : end + 1]
    lows = [body_low(item) for item in window]
    highs = [body_high(item) for item in window]
    atr = atr_series[start] if atr_series and start < len(atr_series) else None
    moved: Decimal | None = None
    if atr is not None and atr > 0:
        with fixed_context():
            moved = (window[-1].close - window[0].open) / atr
    return Leg(
        kind=kind,
        direction=direction,
        start=start,
        end=end,
        low=min(lows),
        high=max(highs),
        volume=sum((item.volume for item in window), Decimal(0)),
        gaps=gaps,
        displacement_atr=moved,
    )


def segment(
    candles: Sequence[Candle],
    atr_series: Sequence[Decimal | None] | None = None,
) -> tuple[Leg, ...]:
    """캔들을 임밸런스·밸런스 마디로 쪼갠다.

    Args:
        candles: `ts` 오름차순 캔들.
        atr_series: 캔들과 **길이가 같은** ATR 계열. 주면 각 마디의 `displacement_atr`
            을 채운다. 탐지에는 쓰이지 않는다 (속성이다).

    Returns:
        시간순 마디들. 임밸런스와 밸런스가 번갈아 나오되, **밸런스는 길이가 0 이면
        생략**된다 (임밸런스가 연달아 붙는 경우).

    Note:
        ## 어떻게 묶는가

        1. 봉마다 몸통 갭을 본다 (`gap_at`)
        2. **같은 방향 갭이 연속되는 동안** 한 임밸런스로 이어 붙인다 — 갭 하나마다
           마디를 끊으면 이미지의 큰 박스가 아니라 잔조각이 나온다
        3. 갭이 끊긴 구간이 밸런스다

        갭 하나는 3봉을 덮으므로 임밸런스의 시작은 `index - 2` 다. 앞 마디와 겹치면
        겹치는 만큼 앞 마디를 줄인다 — 마디는 **서로 겹치지 않아야** 구조가 된다.

        ## ⚠️ 반대 방향 갭은 마디를 끊는다

        상승 임밸런스 도중 하락 갭이 나면 거기서 끊고 새 마디를 연다. 이어 붙이면
        "한 방향으로 몰아쳤다"는 뜻이 사라진다.
    """
    if len(candles) < 3:
        return ()

    legs: list[Leg] = []
    # 아직 마디에 넣지 않은 첫 봉. 여기서부터 다음 마디가 시작한다.
    cursor = 0
    index = 2
    while index < len(candles):
        direction = gap_at(candles, index)
        if direction is LegDirection.FLAT:
            index += 1
            continue

        # 갭이 덮는 3봉의 시작. 앞 마디를 침범하지 않게 자른다.
        begin = max(index - 2, cursor)
        gaps = 1
        end = index
        # 같은 방향 갭이 이어지는 동안 늘린다.
        probe = index + 1
        while probe < len(candles) and gap_at(candles, probe) is direction:
            gaps += 1
            end = probe
            probe += 1

        if begin > cursor:
            # 사이에 낀 구간이 밸런스다 — 갇혀서 못 움직인 자리.
            legs.append(
                _leg(candles, LegKind.BALANCE, LegDirection.FLAT, cursor, begin - 1, 0, atr_series)
            )
        legs.append(_leg(candles, LegKind.IMBALANCE, direction, begin, end, gaps, atr_series))
        cursor = end + 1
        index = probe

    # 🔴 꼬리 구간을 버리지 않는다. 마지막 임밸런스 뒤의 정체가 **지금 진행 중인
    #    밸런스**이며, 전환 기준으로 쓸 가장 최신 구간이 바로 그것이다.
    if cursor < len(candles):
        legs.append(
            _leg(
                candles,
                LegKind.BALANCE,
                LegDirection.FLAT,
                cursor,
                len(candles) - 1,
                0,
                atr_series,
            )
        )
    return tuple(legs)


ZIGZAG_ATR_MULTIPLE = Decimal(3)
"""ZigZag 전환으로 인정할 최소 되돌림 (ATR 배수).

⛔ **안정성 파라미터다 — 성과를 보고 조정 금지** (spec §5.6.5).

3.0 은 Wilder 의 **Volatility Stop** 과 **SuperTrend**(기간 10 · 배수 3)가 쓰는 표준
배수다. "추세 전환으로 인정할 만큼 큰 움직임"의 업계 통용값이며 여기서 지어낸 값이 아니다.

## 왜 % 가 아니라 ATR 인가

트레이딩뷰 ZigZag 기본값은 **편차 5%** 다. 고정 % 는 종목·시간축마다 **다른 뜻**이 된다 —
이 프로젝트가 이미 겪은 문제다 (§1-0h: 고정 % 허용 오차가 1h 0.407xATR / 5m 1.671xATR
로 4.1배 갈렸다). ATR 로 묶으면 변동성이 다른 종목·시간축에서 같은 뜻을 유지한다.
"""


def zigzag(
    candles: Sequence[Candle],
    atr_series: Sequence[Decimal | None],
    multiple: Decimal = ZIGZAG_ATR_MULTIPLE,
) -> tuple[int, ...]:
    """구조적 전환점 — ZigZag (몸통 기준 · ATR 편차).

    Args:
        candles: `ts` 오름차순 캔들.
        atr_series: 캔들과 **길이가 같은** ATR 계열.
        multiple: 전환으로 인정할 되돌림 (ATR 배수).

    Returns:
        전환점 봉 번호들 (오름차순, 고점·저점 교대). ATR 워밍업이 끝나기 전이나
        되돌림이 한 번도 기준을 못 넘으면 빈 튜플이다.

    Note:
        ## 왜 프랙탈이 아니라 ZigZag 인가

        `prior_swings` 는 **Bill Williams 5봉 프랙탈**(좌우 2봉)이고 그 docstring 이
        "성과로 조정하지 않는다"고 못박은 **미세 스윙** 도구다. 실측에서 300봉당 마디가
        51~74개, 마디당 5~7봉이 나왔다 — 사용자 이미지의 ~25봉 박스와 자릿수가 다르다.

        "큰 추세"는 **스케일 상대적**이라 파라미터 없이 정의할 수 없다. 5% 스윙과 0.5%
        스윙은 둘 다 스윙이고, 어느 쪽을 볼지는 정해 줘야 한다. ZigZag 의 편차가 정확히
        그 스케일 손잡이이며, 표준 도구라 임의 발명이 아니다.

        ## 되돌림 기준은 **전환점 시점의 ATR** 이다

        지금 봉의 ATR 을 쓰면 변동성이 커진 뒤에 과거 전환이 취소된다. 극값이 잡힌
        시점의 ATR 로 재면 그 판정이 나중에 흔들리지 않는다 (`_atr_at` 와 같은 이유).

        ## 몸통 기준이다 (사용자 지정)

        꼬리로 재면 스윕 한 번에 전환이 잡힌다 — 그것이 구조 돌파와 유동성 스윕을
        구분하는 `choch_bos.py` 의 판단과 같은 이유다.
    """
    if len(candles) < 3 or len(atr_series) != len(candles):
        return ()

    pivots: list[int] = []
    rising_from = 0  # 지금까지의 최저 몸통 (상승 전환의 기준점)
    falling_from = 0  # 지금까지의 최고 몸통 (하락 전환의 기준점)
    direction: LegDirection = LegDirection.FLAT

    for index in range(1, len(candles)):
        low, high = body_low(candles[index]), body_high(candles[index])

        if direction is not LegDirection.DOWN and low < body_low(candles[rising_from]):
            rising_from = index
        if direction is not LegDirection.UP and high > body_high(candles[falling_from]):
            falling_from = index

        if direction is LegDirection.UP and high > body_high(candles[falling_from]):
            falling_from = index
        if direction is LegDirection.DOWN and low < body_low(candles[rising_from]):
            rising_from = index

        # 되돌림 기준은 극값 시점의 ATR 이다.
        up_atr = atr_series[rising_from]
        down_atr = atr_series[falling_from]

        rose = (
            up_atr is not None
            and up_atr > 0
            and (high - body_low(candles[rising_from]) >= multiple * up_atr)
        )
        if direction is not LegDirection.UP and rose:
            pivots.append(rising_from)
            direction = LegDirection.UP
            falling_from = index
            continue

        fell = (
            down_atr is not None
            and down_atr > 0
            and (body_high(candles[falling_from]) - low >= multiple * down_atr)
        )
        if direction is not LegDirection.DOWN and fell:
            pivots.append(falling_from)
            direction = LegDirection.DOWN
            rising_from = index

    # 마지막 전환 뒤의 극값도 전환점으로 둔다 — 진행 중인 마디의 경계이며, 없으면
    # 그 구간이 통째로 사라진다.
    if direction is LegDirection.UP and falling_from > (pivots[-1] if pivots else -1):
        pivots.append(falling_from)
    elif direction is LegDirection.DOWN and rising_from > (pivots[-1] if pivots else -1):
        pivots.append(rising_from)

    return tuple(sorted(set(pivots)))


def structure(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    params: SwingParams | None = None,
    atr_series: Sequence[Decimal | None] | None = None,
    deviation: Decimal = ZIGZAG_ATR_MULTIPLE,
) -> tuple[Leg, ...]:
    """**스윙 골격** 위에서 마디를 묶는다 — 사용자 이미지 수준의 입자.

    Args:
        candles: `ts` 오름차순 캔들.
        timeframe: 시간축.
        params: 스윙 파라미터. 없으면 표준값.
        atr_series: 캔들과 길이가 같은 ATR 계열 (속성 기록용 + ZigZag 골격).
        deviation: ZigZag 편차 (ATR 배수). ⛔ **보기 전용으로만 바꾼다** — 판정에는
            언제나 기본값 `ZIGZAG_ATR_MULTIPLE` 을 쓴다. 화면에서 밀 수 있게 열어
            두되 그 값이 백테스트·실거래로 흘러가면 눈으로 튜닝하는 것이 된다
            (절대 규칙 #11 · §5.6.2).

    Returns:
        시간순 마디들. 스윙과 스윙 사이가 한 마디이며 겹치지 않는다.

    Note:
        ## 왜 `segment` 로는 부족했나 — 실측이 말해줬다

        3봉 몸통 갭만으로 자르면 BTC 1h 300봉에서 마디가 **93개**, 임밸런스 봉 수
        중앙값이 **3봉**이었다. 사용자 이미지의 박스는 10~20봉짜리 대여섯 개다.
        코인 1h 에서 3봉 갭은 수시로 나므로 그것은 "미세 불균형"이지 구조가 아니다.

        ## 새 임계값을 만들지 않았다

        골격을 `prior_swings`(zigzag)에서 가져온다 — HH/HL 판정(`market_structure.py`)이
        쓰는 **바로 그 골격**이다. 새 손잡이를 만드는 대신 이미 고정된 파라미터를
        재사용한다 (`follow_through.DEFAULT_MAX_HOLD_BARS` 가 `max_age_bars` 를 빌린
        것과 같은 판단).

        ## 분류

        | 조건 | 마디 |
        |---|---|
        | 구간에 **같은 방향 몸통 갭**이 있다 | 임밸런스 |
        | 없다 | 밸런스 (갇혀서 못 갔다) |

        게이트는 여전히 **몸통 갭 하나**다. 스윙은 *어디서 자를지*를 정할 뿐 *무엇을
        임밸런스로 볼지*를 정하지 않는다 — 그 둘을 섞으면 스윙 파라미터가 탐지 개수를
        좌우하는 손잡이가 된다.
    """
    if len(candles) < 3:
        return ()
    # 🔴 골격은 **ZigZag** 다 (ATR 이 있을 때). 프랙탈은 미세 스윙 도구라 마디가
    #    5~7봉으로 잘게 쪼개졌다 — `zigzag` docstring 에 실측이 있다.
    #
    #    ATR 이 없으면 프랙탈로 물러난다: 스케일 손잡이를 쓸 수 없으니 있는 골격으로
    #    최선을 낸다. **조용히 빈 결과를 내지는 않는다** (절대 규칙 #8).
    if atr_series is not None and len(atr_series) == len(candles):
        turns = zigzag(candles, atr_series, deviation)
    else:
        turns = tuple(point.index for point in prior_swings(candles, timeframe, params))
    # 전환점이 모자라면 골격이 없다 — 통째로 한 마디로 두는 편이 조각내는 것보다 정직하다.
    if len(turns) < 2:
        return (
            _leg(candles, LegKind.BALANCE, LegDirection.FLAT, 0, len(candles) - 1, 0, atr_series),
        )

    # 전환점 사이 + 양 끝을 경계로 쓴다. 첫 전환 앞과 마지막 전환 뒤도 버리지 않는다 —
    # 뒤쪽이 **지금 진행 중인 마디**이고 전환 기준으로 쓸 자리가 바로 거기다.
    edges = [0, *turns, len(candles) - 1]
    bounds = sorted({edge for edge in edges if 0 <= edge < len(candles)})

    legs: list[Leg] = []
    for start, stop in pairwise(bounds):
        end = stop if stop == bounds[-1] else stop - 1
        if end < start:
            continue
        # 구간 안에서 난 갭만 센다. 구간을 걸치는 3봉 묶음은 제외한다 — 남의 마디를
        # 끌어와 세면 경계가 뜻을 잃는다.
        gaps = sum(
            1 for at in range(start + 2, end + 1) if gap_at(candles, at) is not LegDirection.FLAT
        )
        # 🔴 마디의 방향은 **종가 대비 시가**다 (몸통 기준). 갭 개수로 정하지 않는다 —
        #    ZigZag 마디는 이미 한 방향 움직임이고 갭은 그 강도의 증거일 뿐이다.
        moved_up = candles[end].close >= candles[start].open
        direction = LegDirection.UP if moved_up else LegDirection.DOWN
        # 🔴 임밸런스·밸런스는 **주 추세 대비**로 갈린다 (사용자 정의). 여기서는 방향만
        #    붙이고 분류는 `label` 이 추세를 주입받아 한다 — 추세 판정기를 둘로 만들지
        #    않으려는 것이다 (§4.16).
        legs.append(_leg(candles, LegKind.IMBALANCE, direction, start, end, gaps, atr_series))
    return tuple(legs)


def dominant(legs: Sequence[Leg]) -> LegDirection:
    """마디들이 만든 **주 추세** 방향 — 방향별 **가격 이동폭** 합.

    Args:
        legs: `structure` 결과.

    Returns:
        상승·하락. 마디가 없을 때만 `FLAT`.

    Note:
        ## 🔴 봉 수로 세다가 틀렸다

        처음엔 방향별 **봉 수**를 셌다. 그러면 **짧고 급한 하락이 길고 완만한 상승을
        이긴다** — 실측에서 화면 대부분이 상승인데 "하락장"으로 판정됐고, 그 결과 상승
        구간 전체가 "추세와 반대" = 밸런스로 칠해졌다.

        더 나쁜 것은 **동점**이었다. 봉 수가 정확히 같으면 `FLAT` 이 나오고, `label` 이
        FLAT 에서 전부 밸런스로 칠하므로 **화면 전체가 balance 로 뜨는** 상태가 됐다
        (사용자 실측: 배수 2.88 에서 재현). 정수를 세면 동점이 흔하다.

        ## 가격 이동폭이 표준이다

        Wilder 의 **DMI/ADX** 가 +DM 과 -DM(방향별 이동 **거리**)을 합해 우세 방향을 정한다.
        같은 발상을 구조 마디에 적용했다 — 마디마다 몸통 고저 폭을 방향별로 더한다.

        연속값이라 동점이 사실상 나지 않고, "많이 움직인 쪽"이라는 뜻도 직관과 맞는다.
    """
    if not legs:
        return LegDirection.FLAT
    with fixed_context():
        ups = sum(
            (leg.high - leg.low for leg in legs if leg.direction is LegDirection.UP),
            Decimal(0),
        )
        downs = sum(
            (leg.high - leg.low for leg in legs if leg.direction is LegDirection.DOWN),
            Decimal(0),
        )
    if ups == downs:
        # 🔴 연속값이 정확히 같은 것은 사실상 마디가 없을 때뿐이다. 그래도 FLAT 을
        #    돌려주면 `label` 이 전부 밸런스로 칠하므로, **마지막 마디의 방향**으로
        #    떨어뜨린다 — 지금 시장이 가는 쪽이 그것이다.
        return legs[-1].direction
    return LegDirection.UP if ups > downs else LegDirection.DOWN


def label(legs: Sequence[Leg], trend: LegDirection) -> tuple[Leg, ...]:
    """마디를 **주 추세 대비**로 임밸런스·밸런스로 나눈다.

    Args:
        legs: `structure` 결과.
        trend: 주 추세 방향. `FLAT` 이면 전부 밸런스다 — 방향이 없으면 "추세를 만드는
            움직임"도 없다.

    Returns:
        `kind` 가 다시 붙은 마디들.

    Note:
        🔴 사용자 정의 그대로다: *"임밸런스는 **주 추세와 같은 방향**을 나타내는
        구간이고, 밸런스 구간은 추세와 **반대되는** 방향으로 형성된 파동."*

        ⚠️ 처음에 이것을 "구조적으로" 해석해 **갭 유무**로 갈랐다가 틀렸다. ZigZag
        마디는 36~50봉이라 그 안에 몸통 갭이 최소 하나는 반드시 있고, 그래서 1h·1d 에서
        **밸런스가 0개**가 나왔다. 사용자 문장이 처음부터 맞았다.

        몸통 갭은 이제 게이트가 아니라 `Leg.gaps` **속성**이다 — 임밸런스의 강도
        증거이며, 게이트로 승격할지는 §4.14 한계 기여가 정한다.
    """
    return tuple(
        Leg(
            kind=(
                LegKind.IMBALANCE
                if trend is not LegDirection.FLAT and leg.direction is trend
                else LegKind.BALANCE
            ),
            direction=leg.direction,
            start=leg.start,
            end=leg.end,
            low=leg.low,
            high=leg.high,
            volume=leg.volume,
            gaps=leg.gaps,
            displacement_atr=leg.displacement_atr,
        )
        for leg in legs
    )


@dataclass(frozen=True, slots=True)
class Break:
    """구조 이탈 — 추세 전환의 근거 (사용자 매매 룰).

    Attributes:
        at: 이탈이 **확정된** 봉 (종가가 기준선을 넘은 첫 봉).
        was: 이탈 전 추세. `now`: 이탈 후 추세.
        level: 깨진 기준선 — 직전 밸런스의 저점(상승 중) 또는 고점(하락 중).
        balance_start: 기준이 된 밸런스 마디의 시작 봉. `balance_end`: 끝 봉.

    Note:
        🔴 **종가 기준이다.** 꼬리만 넘고 종가가 되돌아온 것은 유동성 스윕이지 구조
        돌파가 아니다 — `choch_bos.py` 가 세운 구분을 그대로 쓴다 (§4.2).
    """

    at: int
    was: LegDirection
    now: LegDirection
    level: Decimal
    balance_start: int
    balance_end: int


def broke(
    candles: Sequence[Candle],
    legs: Sequence[Leg],
    trend: LegDirection,
) -> Break | None:
    """직전 밸런스의 기준선을 **종가로** 이탈했는가.

    Args:
        candles: `ts` 오름차순 캔들.
        legs: `label` 을 거친 마디들.
        trend: 지금 추세 방향.

    Returns:
        이탈 사실. 기준이 될 밸런스가 없거나 아직 안 깨졌으면 None.

    Note:
        사용자 규칙 그대로다:

        > 상승 → 하락 전환은 **첫 상승추세의 밸런스 구간 저점이 깨졌을 때**.
        > 반대로 하락 → 상승은 **고점을 뚫어내면서 상승 임밸런스를 가져갈 때**.

        기준 밸런스는 **가장 최근 것**이다 (`last_balance`) — 사용자가 "최신성이 매우
        중요"라고 지정했고, 과거의 더 큰 밸런스를 지키면 시장이 이미 잊은 자리를
        방어하게 된다.

        ⚠️ **그 밸런스가 끝난 뒤의 봉만 본다.** 밸런스 구간 안의 봉으로 판정하면
        구간을 만든 등락 자체가 이탈로 세어진다.

        ⛔ 추세를 여기서 정하지 않는다 — `trend` 를 받아서 "그 전제가 깨졌는가"만
        답한다. 전환 후 무엇을 할지는 읽는 쪽의 몫이다 (§4.16).
    """
    if trend is LegDirection.FLAT:
        return None
    zone = last_balance(legs, trend)
    if zone is None:
        return None

    rising = trend is LegDirection.UP
    level = zone.low if rising else zone.high
    flipped = LegDirection.DOWN if rising else LegDirection.UP

    for index in range(zone.end + 1, len(candles)):
        close = candles[index].close
        if (rising and close < level) or (not rising and close > level):
            return Break(
                at=index,
                was=trend,
                now=flipped,
                level=level,
                balance_start=zone.start,
                balance_end=zone.end,
            )
    return None


def last_balance(legs: Sequence[Leg], direction: LegDirection) -> Leg | None:
    """추세 전환의 기준이 될 **직전 밸런스**.

    Args:
        legs: `segment` 결과.
        direction: 지금 추세 방향. 상승이면 그 저점이, 하락이면 그 고점이 기준이다.

    Returns:
        가장 최근의 밸런스. 없으면 None.

    Note:
        🔴 **최신성이 기준이다** (사용자 지정). 과거의 더 큰 밸런스를 고르면 이미
        시장이 잊은 자리를 지키게 된다.

        ⚠️ 마지막 마디가 진행 중인 밸런스면 그것을 돌려준다 — 아직 이탈하지 않았으므로
        기준으로 쓰는 것이 맞다.

        ⛔ 거래량으로 고르지 않는다. "거래가 많은 밸런스가 더 중요하다"는 그럴듯하지만
        **측정되지 않은 가설**이고, 여기서 쓰면 그것이 곧 손잡이가 된다. 거래량은
        `Leg.volume` 에 실려 있으니 한계 기여로 재고 나서 정한다.
    """
    del direction  # 방향은 호출부가 low/high 중 무엇을 볼지 정하는 데 쓴다.
    for leg in reversed(legs):
        if leg.kind is LegKind.BALANCE:
            return leg
    return None
