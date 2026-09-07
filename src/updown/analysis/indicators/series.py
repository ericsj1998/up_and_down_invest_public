"""지표 계산 계약 — 입력 시리즈와 워밍업 규칙 (P1-2-1 · spec §6.1, §2.1).

## 계약 세 줄

1. **출력 길이 == 입력 길이.** 워밍업 구간은 `None` 으로 채우고 **잘라내지 않는다**
2. **`None` 은 "아직 판단할 수 없음"이다.** 0 을 반환하지 않는다
3. **모든 계산은 고정 Decimal 컨텍스트**에서 한다 (`common.numeric`)

### 1번이 가장 중요하다 — 인덱스가 좌표계다

`structures/` 는 **봉 번호를 x축으로** 쓴다 (추세선 기울기, 접점 인덱스, 합류 판정).
지표가 워밍업 구간을 잘라 반환하면 `atr[0]` 이 `candles[13]` 에 대응하게 되고, 그
오차 13은 조용히 전파된다 — 손절가가 13봉 전 ATR 로 계산되어도 **아무 예외도 나지
않는다.** 그래서 길이를 맞추는 것이 편의가 아니라 안전장치다.

## 왜 numpy/pandas 를 런타임에 쓰지 않는가 ⚠️

spec §2.1 은 "자체 구현 (numpy/pandas)"이라고 적었지만, 그 괄호를 따르면 **P0-3 이
확정한 `Indicators` 계약과 충돌한다**:

- `Indicators.atr14` 는 `Decimal` 이다. `stop = entry - k*ATR` 로 **가격과 직접 연산**되고
  그 결과가 호가단위 라운딩·RR 재검증(§12.2)을 거쳐 실주문 가격이 된다
- numpy 는 float64 다. float64 로 계산해 Decimal 로 되돌리면 이진 오차가 손절가에
  들어온다. 1e8 규모 원화 가격에서 상대오차 1e-16 은 약 1원이며 호가단위(BTC 1,000원)에
  흡수되지만, **오차를 넣을 이유가 없다**

→ 지표 코어는 **Decimal + 순수 파이썬**이다. numpy/pandas/pandas-ta 는 **dev 의존성**으로만
두고 정답지 대조 테스트에서만 쓴다 (P1-2 DoD 2 를 더 강하게 만족한다).

괄호를 어긴 것이지 원칙을 어긴 것이 아니다 — §2.1 의 요지는 "라이브러리에 **위임**하지
말라"이고 그것은 지킨다 (절대 규칙 #9).

## 결측 구간을 지표는 건너 계산한다 (구조물과 다르다)

추세선은 **기울기가 봉당 변화량**이라 없는 봉이 있으면 무의미해져 구멍을 가로지르지
않는다(`docs/rules/structure_rules.md` §4). 지표는 다르다 — "최근 N봉 종가의 평균"은 그 N봉이
실제 시간상 얼마나 벌어져 있든 **정의가 성립**한다.

Phase 0 의 결측 8건은 전부 거래소 점검으로 확인돼 `ignored` 로 내려갔으므로 §7 의
"분석 차단" 대상(`status='open'`)이 아니다. 다만 백테스트는 이 상태를 함께 기록해야
한다 (Phase 0 인계 사항).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.numeric import fixed_context


class SeriesError(ValueError):
    """입력 시리즈가 계약을 위반했다.

    Note:
        조용히 정렬하거나 중복을 버리지 않는다 (절대 규칙 #8). 순서가 틀린 캔들로
        계산한 지표는 값이 그럴듯해서 틀린 것을 알 수 없다.
    """


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """지표 계산 입력 — 캔들에서 뽑은 검증된 시리즈.

    Attributes:
        instrument: 대상 종목.
        timeframe: 시간축.
        ts: 봉 시작 시각들 (UTC, 오름차순).
        open: 시가 열.
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        volume: 거래량 열.

    Note:
        검증을 **한 번만** 하기 위한 타입이다. 지표 함수마다 캔들을 받아 매번 정렬·
        중복을 확인하면 같은 검사가 반복되고, 빠뜨린 함수가 생긴다.

        모든 열의 길이가 같고 인덱스가 봉 번호에 그대로 대응한다 — `structures/` 와
        같은 좌표계다 (모듈 docstring 1번).
    """

    instrument: Instrument
    timeframe: Timeframe
    ts: tuple[datetime, ...]
    open: tuple[Decimal, ...]
    high: tuple[Decimal, ...]
    low: tuple[Decimal, ...]
    close: tuple[Decimal, ...]
    volume: tuple[Decimal, ...]

    def __len__(self) -> int:
        """봉 수."""
        return len(self.ts)


def from_candles(candles: Sequence[Candle]) -> PriceSeries:
    """캔들에서 계산 입력을 만든다 — 여기서 계약을 검증한다.

    Args:
        candles: `ts` 오름차순 캔들. 같은 종목·같은 시간축이어야 한다.

    Returns:
        검증된 시리즈.

    Raises:
        SeriesError: 비었거나, 종목·시간축이 섞였거나, 정렬이 깨졌거나, `ts` 가
            중복된 경우.

    Note:
        결측(빠진 봉)은 **오류가 아니다.** 거래소 점검으로 실제로 생기며 지표는 건너
        계산한다 (모듈 docstring). 오류로 보는 것은 "있어야 할 순서가 틀린" 경우다.
    """
    if not candles:
        raise SeriesError("빈 캔들로는 지표를 계산할 수 없다")
    first = candles[0]
    for position, candle in enumerate(candles):
        if candle.instrument != first.instrument:
            raise SeriesError(
                f"종목이 섞였다: index {position} 이 {candle.instrument.symbol}, "
                f"첫 봉은 {first.instrument.symbol}"
            )
        if candle.timeframe is not first.timeframe:
            raise SeriesError(
                f"시간축이 섞였다: index {position} 이 {candle.timeframe}, "
                f"첫 봉은 {first.timeframe}"
            )
        if position > 0 and candle.ts <= candles[position - 1].ts:
            raise SeriesError(
                f"ts 가 오름차순이 아니다: index {position} ({candle.ts}) 가 "
                f"직전({candles[position - 1].ts}) 이하다"
            )
    return PriceSeries(
        instrument=first.instrument,
        timeframe=first.timeframe,
        ts=tuple(candle.ts for candle in candles),
        open=tuple(candle.open for candle in candles),
        high=tuple(candle.high for candle in candles),
        low=tuple(candle.low for candle in candles),
        close=tuple(candle.close for candle in candles),
        volume=tuple(candle.volume for candle in candles),
    )


def require_period(period: int, name: str = "period") -> None:
    """기간 파라미터를 검증한다.

    Args:
        period: 검증할 기간.
        name: 오류 메시지에 쓸 이름.

    Raises:
        SeriesError: 1 미만인 경우.
    """
    if period < 1:
        raise SeriesError(f"{name} 는 1 이상이어야 한다 — 받은 값: {period}")


def simple_average(values: Sequence[Decimal], start: int, period: int) -> Decimal:
    """`values[start:start+period]` 의 산술평균.

    Args:
        values: 값 열.
        start: 시작 번호.
        period: 창 길이.

    Returns:
        고정 컨텍스트로 나눈 평균 — 정밀도가 환경에 따라 달라지면 같은 입력이 다른
        출력을 낸다 (규칙 #5).
    """
    total = sum(values[start : start + period], Decimal(0))
    with fixed_context():
        return total / Decimal(period)


def wilder_average(
    values: Sequence[Decimal | None],
    period: int,
) -> list[Decimal | None]:
    """Wilder 평활(RMA) — ATR·RSI 가 공유하는 평균 방식.

    Args:
        values: 원 시리즈. 선행 `None`(예: 첫 봉의 True Range)을 허용한다.
        period: 평활 기간.

    Returns:
        입력과 **같은 길이**의 결과. 워밍업 구간은 `None` 이다.

    Raises:
        SeriesError: `period` 가 1 미만이거나 `None` 이 시리즈 중간에 있는 경우.

    Note:
        Wilder 의 정의를 그대로 쓴다 — 첫 값은 **단순평균으로 시드**하고 이후
        `rma[i] = (rma[i-1] * (period-1) + value[i]) / period` 로 재귀한다.

        시드를 단순평균으로 두는 것이 차트 플랫폼 관행이자 Wilder 원문이다. 첫 값을
        그대로 시드하는 변형(순수 EWM)도 있으며 초반 몇십 봉에서 값이 다르다 —
        수렴하므로 후반부는 같다. 대조 테스트가 이 차이를 명시적으로 다룬다.

        **중간의 `None` 은 거부한다.** 결측 봉은 애초에 시리즈에 행이 없으므로
        중간 `None` 은 계산 버그를 뜻한다. 조용히 건너뛰면 그 버그가 숨는다.
    """
    require_period(period)
    lead = 0
    while lead < len(values) and values[lead] is None:
        lead += 1
    body: list[Decimal] = []
    for position in range(lead, len(values)):
        value = values[position]
        if value is None:
            raise SeriesError(
                f"시리즈 중간(index {position})에 None 이 있다 — 결측 봉은 행이 아예 "
                f"없으므로 이것은 계산 버그다"
            )
        body.append(value)

    result: list[Decimal | None] = [None] * len(values)
    if len(body) < period:
        return result
    current = simple_average(body, 0, period)
    result[lead + period - 1] = current
    with fixed_context():
        divisor = Decimal(period)
        weight = Decimal(period - 1)
        for offset in range(period, len(body)):
            current = (current * weight + body[offset]) / divisor
            result[lead + offset] = current
    return result
