"""지표 시리즈 계산과 `Indicators` 조립 (P1-2 · spec §6.1, §4.3).

## 왜 시리즈와 스냅샷을 함께 두는가

두 소비자의 요구가 다르다:

- **`MarketContext`**(spec §4.3.1)는 **한 봉의** `Indicators` 를 원한다
- **백테스트**(spec §4.11)는 봉을 걸어가며 매 시점의 값을 읽어야 한다

시리즈를 한 번 계산해 두고 `at(index)` 로 스냅샷을 뽑으면 둘을 같은 계산으로 만족시킨다.
백테스트가 봉마다 전체 재계산을 하면 O(n²) 이 되고, 반대로 스냅샷만 제공하면 백테스트가
자체 캐시를 만들어 **계산이 두 곳으로 갈라진다.**

## 미래 참조 금지 (lookahead)

`at(index)` 는 `index` 봉까지의 정보로만 만들어진 값을 돌려준다. 지표 시리즈가
그 성질을 이미 갖고 있다 — SMA·EMA·Wilder 평활·거래량 배수 전부 **과거만** 본다.
그래서 `at()` 은 잘라내기가 아니라 단순 조회다.

단 **`crosses`·`divergences` 는 목록 전체**를 들고 있으므로, 특정 시점 기준으로 쓰려면
`crosses_until(index)` / `divergences_until(index)` 를 써야 한다. 전체 목록을 그대로
넘기면 미래의 사건이 보인다.

## 확인 지연 (confirmation lag) ⚠️

크로스와 다이버전스의 성질이 다르다:

| | 언제 알 수 있는가 |
|---|---|
| 크로스 | **그 봉에서 즉시** — 이동평균 값은 봉마감으로 확정된다 |
| 다이버전스 | **스윙 확인 봉수만큼 뒤** — 우측 `right_bars` 개 봉이 있어야 극값이 확정된다 |

즉 index 249 의 스윙에서 성립한 다이버전스는 **index 251 이 되어야 알 수 있다.**
`second_index <= position` 으로만 거르면 2봉 앞선 정보를 쓰게 되고, 5m 이면 10분의
예지력이다. 이런 미세한 미래 참조가 백테스트를 실거래보다 좋게 만든다.

→ `divergences_until()` 이 `confirmation_lag_bars` 를 적용한다. P1-3 의 "전체 계산 vs
잘라낸 계산 동치성" 테스트가 이 누락을 실제로 잡아냈다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators import atr as atr_module
from updown.analysis.indicators import volume as volume_module
from updown.analysis.indicators.ma import (
    CROSS_PAIRS,
    STANDARD_PERIODS,
    Cross,
    MaAlignment,
    alignment,
    ema,
    find_crosses,
    sma,
)
from updown.analysis.indicators.rsi import Divergence, find_divergences
from updown.analysis.indicators.rsi import rsi as compute_rsi
from updown.analysis.indicators.series import SeriesError, from_candles
from updown.analysis.structures.params import SwingParams
from updown.analysis.structures.swing import prior_swings
from updown.common.domain.candle import Candle
from updown.common.domain.reports import Indicators


@dataclass(frozen=True, slots=True)
class IndicatorSeries:
    """전 봉 지표 시리즈.

    Attributes:
        length: 봉 수. 모든 시리즈가 이 길이다.
        sma: 기간 → SMA 시리즈.
        ema: 기간 → EMA 시리즈.
        atr14: ATR(14) 시리즈.
        rsi14: RSI(14) 시리즈.
        volume_ratio: 거래량 배수 시리즈.
        crosses: 골든/데드크로스 전체 목록.
        divergences: 다이버전스 전체 목록.
        confirmation_lag_bars: 스윙 확인에 필요한 우측 봉 수. 다이버전스가 **몇 봉 뒤에야
            알 수 있는지**를 나타낸다 (모듈 docstring).

    Note:
        모든 시리즈의 길이가 `length` 와 같다 — 인덱스가 봉 번호에 그대로 대응하며
        `structures/` 와 같은 좌표계다 (`series` 모듈 계약 1번).
    """

    length: int
    sma: dict[int, list[Decimal | None]]
    ema: dict[int, list[Decimal | None]]
    atr14: list[Decimal | None]
    rsi14: list[float | None]
    volume_ratio: list[float | None]
    crosses: tuple[Cross, ...]
    divergences: tuple[Divergence, ...]
    confirmation_lag_bars: int

    def at(self, index: int) -> Indicators:
        """특정 봉의 `Indicators` 스냅샷 (spec §4.3).

        Args:
            index: 봉 번호. 음수는 파이썬 관례대로 뒤에서 센다.

        Returns:
            그 봉의 지표. 워밍업 구간이면 필드가 None 이다.

        Raises:
            SeriesError: 범위를 벗어난 인덱스.

        Note:
            `macd`/`bb`/`vwap` 은 항상 None 이다 — spec §6.2 권장(Phase 2) 항목이다.
        """
        position = index if index >= 0 else self.length + index
        if not 0 <= position < self.length:
            raise SeriesError(f"index {index} 가 범위(0~{self.length - 1}) 밖이다")
        return Indicators(
            ma20=self.sma[20][position],
            ma60=self.sma[60][position],
            ma120=self.sma[120][position],
            ma200=self.sma[200][position],
            ema20=self.ema[20][position],
            ema60=self.ema[60][position],
            ema120=self.ema[120][position],
            ema200=self.ema[200][position],
            rsi14=self.rsi14[position],
            macd=None,
            bb=None,
            atr14=self.atr14[position],
            vwap=None,
            volume_ratio=self.volume_ratio[position],
        )

    def alignment_at(self, index: int) -> MaAlignment | None:
        """특정 봉의 SMA 배열 상태 (spec §6.1).

        Args:
            index: 봉 번호.

        Returns:
            정배열/역배열/혼재. 워밍업으로 하나라도 없으면 None 이다.

        Raises:
            SeriesError: 범위를 벗어난 인덱스.

        Note:
            SMA 기준으로 판정한다. EMA 배열도 계산 가능하지만 spec §6.1 의
            "200일선 위/아래" 필터가 관행상 SMA 이므로 기본을 SMA 로 둔다.
        """
        position = index if index >= 0 else self.length + index
        if not 0 <= position < self.length:
            raise SeriesError(f"index {index} 가 범위(0~{self.length - 1}) 밖이다")
        return alignment([self.sma[period][position] for period in STANDARD_PERIODS])

    def crosses_until(self, index: int) -> tuple[Cross, ...]:
        """`index` 봉까지 발생한 크로스만 (미래 참조 금지 — 모듈 docstring).

        Args:
            index: 기준 봉 번호.

        Returns:
            해당 봉 이하에서 확정된 크로스들.
        """
        position = index if index >= 0 else self.length + index
        return tuple(cross for cross in self.crosses if cross.index <= position)

    def divergences_until(self, index: int) -> tuple[Divergence, ...]:
        """`index` 봉 시점에 **알 수 있는** 다이버전스만 (확인 지연 반영).

        Args:
            index: 기준 봉 번호.

        Returns:
            `second_index + confirmation_lag_bars <= index` 인 다이버전스들.

        Note:
            **`second_index <= index` 가 아니다.** 스윙은 우측 확인 봉이 있어야 극값임이
            확정되므로, index 249 의 스윙에서 성립한 다이버전스는 251 이 되어야 알 수
            있다 (모듈 docstring "확인 지연"). 지연을 빼먹으면 5m 에서 10분의 예지력이
            생기고, 그 미세한 미래 참조가 백테스트를 실거래보다 좋게 만든다.
        """
        position = index if index >= 0 else self.length + index
        return tuple(
            item
            for item in self.divergences
            if item.second_index + self.confirmation_lag_bars <= position
        )


def compute(
    candles: Sequence[Candle],
    swing_params: SwingParams | None = None,
) -> IndicatorSeries:
    """캔들 전체의 지표를 한 번에 계산한다 (spec §6.1).

    Args:
        candles: `ts` 오름차순 캔들. 같은 종목·같은 시간축이어야 한다.
        swing_params: 다이버전스용 스윙 파라미터. None 이면 표준값.

    Returns:
        전 봉 지표 시리즈.

    Raises:
        SeriesError: 입력이 계약을 위반한 경우 (`series.from_candles` 검증).

    Note:
        **캔들을 받는다** — `PriceSeries` 가 아니다. 다이버전스가 스윙 탐지를 거치고
        스윙 탐지는 결측 판정을 위해 캔들이 필요하기 때문이다. 시리즈만 받으면 캔들을
        되돌려 만들어야 하고, 그 재구성은 순수한 낭비다.
    """
    series = from_candles(candles)
    swing_settings = swing_params or SwingParams()
    simple = {period: sma(series.close, period) for period in STANDARD_PERIODS}
    exponential = {period: ema(series.close, period) for period in STANDARD_PERIODS}
    ratios = volume_module.volume_ratio(series.volume)
    rsi_values = compute_rsi(series.close)

    crosses: list[Cross] = []
    for fast_period, slow_period in CROSS_PAIRS:
        crosses.extend(
            find_crosses(
                simple[fast_period],
                simple[slow_period],
                series.ts,
                fast_period,
                slow_period,
                ratios,
            )
        )
    crosses.sort(key=lambda cross: (cross.index, cross.fast_period))

    swings = prior_swings(candles, series.timeframe, swing_settings)
    divergences = find_divergences(swings, rsi_values)

    return IndicatorSeries(
        length=len(series),
        sma=simple,
        ema=exponential,
        atr14=atr_module.atr(series.high, series.low, series.close),
        rsi14=rsi_values,
        volume_ratio=ratios,
        crosses=tuple(crosses),
        divergences=tuple(divergences),
        confirmation_lag_bars=swing_settings.right_bars,
    )
