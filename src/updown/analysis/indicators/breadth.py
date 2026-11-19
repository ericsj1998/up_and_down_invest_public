"""시장 전체 값 — 종목들의 일봉 종가 표에서 "종목 폭" 과 "뒤처진 묶음의 최근 수익" 을 낸다 (순수).

세션의 진입 문(`entry_washed`)이 읽는 값 가운데 종목 하나로는 알 수 없는 둘이다. 세션은 종목
하나만 알고 네트워크를 모르므로(원칙 P3) 러너가 이 함수의 결과를 사실 입력으로 넣는다
(`Session.market_breadth` · `Session.market_lag_ret`).

종가 표를 어디서 받는지는 이 모듈이 모른다 — 어느 거래소의 어느 종목 묶음이든 같은 식이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

MIN_LAG_SYMBOLS = 30
"""뒤처진 묶음을 고를 수 있는 최소 종목 수 — 이보다 적으면 값을 내지 않는다(모름)."""


@dataclass(frozen=True, slots=True)
class MarketValues:
    """시장 전체 값 한 벌. 낼 수 없는 값은 None(모름 — 문은 보류한다).

    Attributes:
        breadth: 이평 값이 있는 종목 가운데 종가가 이평 위인 비율(0 ~ 1).
        lag_ret: 뒤처진 묶음의 최근 수익률(묶음 평균).
        breadth_count: 종목 폭에 든 종목 수(관측용).
        lag_count: 뒤처진 묶음을 고른 모집단 종목 수(관측용).
    """

    breadth: Decimal | None
    lag_ret: Decimal | None
    breadth_count: int
    lag_count: int


def market_values(
    closes: Mapping[str, Sequence[Decimal | None]],
    *,
    ma_days: int,
    lag_days: int,
    rank_days: int,
) -> MarketValues:
    """종목별 일봉 종가 줄에서 종목 폭과 뒤처진 묶음의 최근 수익을 낸다.

    Args:
        closes: 종목 → 일봉 종가 줄. **모든 종목이 같은 날 줄에 맞춰져 있다**(오름차순 · 마지막
            칸 = 마지막으로 닫힌 날) · 그 날 값이 없으면 None.
        ma_days: 종목 폭을 재는 이평 일수(20).
        lag_days: 뒤처진 묶음의 최근 수익 일수(3).
        rank_days: 뒤처진 묶음을 고르는 수익 일수(30) — `lag_days` 일 전까지의 수익으로 줄 세운다.

    Returns:
        `MarketValues`. 종목 폭은 마지막 `ma_days` 칸이 다 차 있는 종목만으로 잰다(하나도 없으면
        None). 뒤처진 묶음은 "`lag_days` 일 전 종가 ÷ `lag_days + rank_days` 일 전 종가 - 1" 이
        낮은 순으로 아래 1/3(버림)이고, 값은 그 묶음의 "마지막 종가 ÷ `lag_days` 일 전 종가 - 1"
        평균이다. 두 수익을 다 낼 수 있는 종목이 `MIN_LAG_SYMBOLS` 개보다 적으면 None.

    Raises:
        ValueError: 일수가 1 미만이거나 종목마다 줄 길이가 다른 경우.

    Note:
        같은 수익의 종목은 **종목 이름 순**으로 줄 세운다(결정론 · 규칙 #5).
        최근 수익을 재는 구간과 줄 세우는 구간이 겹치지 않는다 — "이미 뒤처져 있던 종목이 최근
        며칠 더 내렸나" 를 본다.
    """
    if min(ma_days, lag_days, rank_days) < 1:
        raise ValueError("일수는 1 이상")
    lengths = {len(row) for row in closes.values()}
    if len(lengths) > 1:
        raise ValueError(f"종목마다 줄 길이가 다르다: {sorted(lengths)[:4]}")
    above = total = 0
    ranked: list[tuple[Decimal, str, Decimal]] = []
    span = lag_days + rank_days
    for name in sorted(closes):
        row = closes[name]
        if len(row) >= ma_days:
            window = row[-ma_days:]
            if all(x is not None for x in window):
                level = sum((x for x in window if x is not None), Decimal(0)) / ma_days
                last = window[-1]
                total += 1
                above += int(last is not None and last > level)
        if len(row) > span:
            now_, mid, far = row[-1], row[-1 - lag_days], row[-1 - span]
            if now_ is not None and mid is not None and far is not None and mid > 0 and far > 0:
                ranked.append((mid / far - 1, name, now_ / mid - 1))
    breadth = None if total == 0 else Decimal(above) / Decimal(total)
    lag_ret: Decimal | None = None
    if len(ranked) >= MIN_LAG_SYMBOLS:
        ranked.sort(key=lambda item: (item[0], item[1]))
        basket = ranked[: len(ranked) // 3]
        lag_ret = sum((item[2] for item in basket), Decimal(0)) / len(basket)
    return MarketValues(
        breadth=breadth, lag_ret=lag_ret, breadth_count=total, lag_count=len(ranked)
    )
