"""거래량 기준선 — **같은 시간대끼리 비교한다** (T03).

## 왜 20봉 평균이 틀렸나

거래량에는 **주기가 있다.** 직전 20봉 평균은 그 주기를 뭉갠다.

```
주식   개장 직후 봉은 어느 날이든 거래량이 많다
       20봉 평균과 비교하면 **매일 "급증"으로 잡힌다**
코인   아시아·유럽·미국 세션에 따라 하루 안에서 몇 배씩 오르내린다
       + 주말은 평일보다 조용하다
```

⇒ "지금이 평소보다 많은가"를 물으려면 **평소가 같은 시간대여야** 한다.

## 🔴 주기가 시장마다 다르다 — 같은 문장, 다른 구현

| | 주기 | "같은 시간대"란 |
|---|---|---|
| 주식 | 하루 안 위치 | 매일 같은 시각의 봉끼리 (개장 후 n번째 봉) |
| 코인 | UTC 시각 + 요일 | 매주 같은 요일·같은 시각의 봉끼리 |

코인에 요일을 넣는 이유: 24시간 장이라 "개장 후 n번째"가 없고, 주말 거래량이 평일과
뚜렷이 다르다. 요일을 빼면 주말이 평일 기준선에 눌려 항상 "거래량 부족"으로 잡힌다.

## ⛔ 이 값이 바뀌면 기존 실측이 무효가 된다

오더블록 연 514건, 이행률, 한계 기여가 전부 옛 기준선에서 나온 숫자다. 그래서 이것이
[부록 B 1번](../../../../docs/strategy/judgement_spec.md)이고, 새 지표를 얹기 **전에**
해야 한다 — 나중에 바꾸면 무엇 때문에 숫자가 변했는지 못 가른다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Market
from updown.common.numeric import fixed_context

MIN_SAMPLES = 4
"""기준선을 낼 최소 표본 수 (같은 시간대 과거 봉).

⚠️ **근거가 약한 값이다.** 표본이 적으면 기준선이 한두 봉에 휘둘리고, 크게 잡으면
워밍업이 길어져 앞자락을 통째로 못 쓴다. 4주(코인)·4일(주식)에 해당한다.

⛔ 성과를 보고 조정하지 않는다. 바꾸려면 축으로 올려 판정한다 (절대 규칙 #12).
"""


def _median(values: list[Decimal]) -> Decimal:
    """기준선 — **평균이 아니라 중앙값**.

    Args:
        values: 같은 슬롯의 과거 거래량.

    Returns:
        중앙값.

    Note:
        🔴 **거래량은 롱테일 분포다.** 표본 4개 중 하나만 10배 스파이크여도 평균
        기준선이 3배 뜨고, 그러면 이후 봉이 전부 "거래량 부족"으로 보인다.

        실측이 그것을 보여 줬다 — 평균 기준선에서 코인 배수의 **중앙값이 0.46** 이었다.
        기준선이 맞다면 1.0 근처여야 한다. 중앙값으로 바꾼 근거는 성과가 아니라
        **분포의 성질**이다 (§5.6.2 자동조율이 아니다).
    """
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def slot_of(market: Market, moment: datetime) -> tuple[int, ...]:
    """이 봉이 속한 **시간대 슬롯**.

    Args:
        market: 시장.
        moment: 봉 시작 시각 (UTC aware).

    Returns:
        같은 슬롯이면 같은 값이 나오는 키.

    Note:
        🔴 **코인은 `(요일, 시, 분)`, 주식은 `(시, 분)`** 이다.

        코인에 요일을 넣는 이유는 주말이다. 24시간 장이라 주말도 거래되는데 평일보다
        조용하고, 요일을 빼면 주말 봉이 평일 기준선에 눌려 **항상 "거래량 부족"** 으로
        잡힌다.

        주식에 요일을 안 넣는 이유는 표본이다. 거래일이 주 5일뿐이라 요일까지 나누면
        같은 슬롯이 **주 1개**가 되어 `MIN_SAMPLES` 를 채우는 데 한 달이 걸린다.

        ⚠️ 주식은 시각을 **현지시각으로 바꿔야** 정확하다. 서머타임 때문에 UTC 시각이
        계절마다 한 시간 밀리기 때문이다. 지금은 UTC 로 두되 이 한계를 적어 둔다 —
        `session.py` 의 달력을 받는 형태로 바꾸는 것이 다음 단계다.
    """
    if market is Market.UPBIT:
        return (moment.weekday(), moment.hour, moment.minute)
    return (moment.hour, moment.minute)


def seasonal_ratio(candles: Sequence[Candle], market: Market) -> list[float | None]:
    """같은 시간대 평균 대비 거래량 배수.

    Args:
        candles: 봉 (`ts` 오름차순, 같은 종목·시간축).
        market: 주기를 정하는 시장.

    Returns:
        봉마다 배수. 표본이 모자라거나 기준선이 0 이면 **None**.

    Note:
        🔴 **자기 자신을 분모에 넣지 않는다.** 같은 슬롯의 **과거** 봉만 쓴다.
        당해 봉을 포함하면 폭발한 거래량이 자기 분모를 끌어올려 배수가 축소된다
        (`volume.py` 가 같은 판단을 했다).

        ⛔ 표본이 모자랄 때 **0 이나 1.0 으로 채우지 않는다.** "배수가 1이다"와
        "배수를 모른다"는 다르고, 채우면 워밍업 구간이 전부 "평범한 거래량"이 되어
        그 구간의 급증 판정이 조용히 죽는다 (절대 규칙 #8).

        ⚠️ 기준선이 0 인 경우(그 시간대에 거래가 전혀 없던 종목)도 None 이다.
        0 으로 나눌 수 없고, "무한대 배수"는 급증이 아니라 **데이터 부재**다.
    """
    history: dict[tuple[int, ...], list[Decimal]] = {}
    out: list[float | None] = []
    with fixed_context():
        for candle in candles:
            slot = slot_of(market, candle.ts)
            past = history.setdefault(slot, [])
            if len(past) < MIN_SAMPLES:
                out.append(None)
            else:
                baseline = _median(past)
                out.append(float(candle.volume / baseline) if baseline > 0 else None)
            past.append(candle.volume)
    return out


@dataclass(frozen=True, slots=True)
class VolumeBaseline:
    """거래량 배수를 **시각으로** 찾는 색인.

    Attributes:
        ratios: 봉 시작 시각 → 배수. 표본이 모자란 봉은 값이 **없다**.

    Note:
        ## 🔴 왜 색인이 필요한가 — 창 안에서는 계산할 수 없다

        탐지 창은 400봉이고, 15m 기준 **4.2일**이다. 그런데 코인 슬롯은 `(요일, 시, 분)`
        이라 표본 4개를 채우려면 **4주**가 걸린다.

        ⇒ 창 안에서 계산하면 코인은 배수가 **전부 None** 이 된다. 그리고 그것은 예외를
        내지 않고 조용히 "거래량 근거 0건"으로 나타난다 (절대 규칙 #8 이 막으려는 형태다).

        **"평소 거래량"은 4일로 알 수 없다.** 그것은 창의 성질이 아니라 **종목의 성질**
        이므로 전체 이력에서 한 번 계산해 조회한다.

        ## 미래 참조가 아니다

        `seasonal_ratio` 는 봉 i 의 배수를 **같은 슬롯의 과거 봉만으로** 만든다. 그러므로
        전체를 미리 계산해 두고 나중에 조회해도 봉 i 가 미래를 본 적이 없다.
        `gates.trend_gate.TrendLookup` 이 같은 근거로 같은 구조를 쓴다.

        ## ⛔ 봉 시작 시각으로 색인한다 — `TrendLookup` 과 다르다

        `TrendLookup` 은 **마감** 시각으로 색인한다. 상위 TF 봉이 아직 진행 중일 수 있어서
        시작 시각으로 찾으면 미래를 보기 때문이다.

        여기는 **같은 시간축의 같은 봉**이라 그 문제가 없다. 봉 i 의 거래량 배수는 봉 i 가
        마감돼야 확정되지만, 탐지기가 보는 창의 마지막 봉은 이미 마감된 봉이다.
    """

    ratios: Mapping[datetime, float]

    @classmethod
    def build(cls, candles: Sequence[Candle], market: Market) -> VolumeBaseline:
        """전체 이력에서 색인을 만든다.

        Args:
            candles: **전체** 캔들 (`ts` 오름차순, 같은 종목·시간축).
            market: 주기를 정하는 시장.

        Returns:
            색인.

        Note:
            ⚠️ **전체를 넣어야 한다.** 창을 넣으면 이 자료형을 만든 이유가 없어진다.
        """
        series = seasonal_ratio(candles, market)
        return cls(
            ratios={
                candle.ts: value
                for candle, value in zip(candles, series, strict=True)
                if value is not None
            }
        )

    def at(self, ts: datetime) -> float | None:
        """그 봉의 거래량 배수.

        Args:
            ts: 봉 시작 시각.

        Returns:
            배수. 표본이 모자랐던 봉이면 None.
        """
        return self.ratios.get(ts)

    def series_for(self, candles: Sequence[Candle]) -> list[float | None]:
        """창에 맞춘 배수 계열.

        Args:
            candles: 탐지 창.

        Returns:
            창과 **길이가 같은** 계열.

        Note:
            🔴 길이를 맞춰 돌려주는 이유는 정렬 사고를 막기 위해서다. 짧은 쪽에 맞추면
            **엉뚱한 봉의 배수로 판정하면서 예외가 나지 않는다** (`AtrTolerance` 가 같은
            경고를 달고 있다).
        """
        return [self.ratios.get(candle.ts) for candle in candles]
