"""기준 종목(BTC) 국면값 — 마감된 4H 봉에서 세션의 진입 문이 읽는 값 (T309 ① · 순수).

실계좌 러너(`LiveRunner._inject_ref_regime`)가 4H 마감마다 부르던 계산을 그대로 꺼냈다.
펀드 재현 도구(T309)가 **같은 식**을 과거 봉에서 부르려면 비동기 · 벽시계 코드 밖에
있어야 한다 — 식이 두 벌이면 재현이 아니다.

- `above` (`entry_ref_ma_gate` n): 마지막 종가 > SMA(n).
- `ret` (`entry_ref_return_band.bars` n): 마지막 종가 ÷ n 봉 전 종가 - 1 (T290 국면 띠).
- `surge` (`entry_ref_surge_cap.days` d): UTC 일봉 종가(00:00 에 끝나는 4H 봉)의
  d 일 수익률 (T304 #8).
- `sma_down` (`entry_ref_sma_down` bars · lag): SMA(bars) 가 lag 봉 전보다 낮다
  (T304 #2 · 연구 R2).
- `vol` (`entry_vol_target.days` d): UTC 일봉 로그수익 d 개의 모집단 표준편차 x √365 ·
  최근 3 일 치 (T304 변동성 목표).
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from updown.analysis.indicators.ma import sma

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from updown.analysis.playbook.types import Playbook
    from updown.common.domain.candle import Candle

FOUR_HOURS = timedelta(hours=4)


@dataclass(frozen=True, slots=True)
class RefNeeds:
    """어떤 국면값이 필요한가 — 세션에 실린 플레이북 선언에서 모은다. None 이면 그 값은 안 낸다.

    Attributes:
        ma_n: `entry_ref_ma_gate` 기간.
        band_n: `entry_ref_return_band.bars`.
        surge_days: `entry_ref_surge_cap.days`.
        sma_bars: `entry_ref_sma_down.bars`.
        sma_lag: `entry_ref_sma_down.lag`.
        vol_days: `entry_vol_target.days`.
    """

    ma_n: int | None = None
    band_n: int | None = None
    surge_days: int | None = None
    sma_bars: int | None = None
    sma_lag: int | None = None
    vol_days: int | None = None

    @property
    def empty(self) -> bool:
        """아무 값도 필요 없다 — 러너는 봉을 받으러 가지도 않는다."""
        return (
            self.ma_n is None
            and self.band_n is None
            and self.surge_days is None
            and self.sma_bars is None
            and self.vol_days is None
        )

    @property
    def bars(self) -> int:
        """계산에 필요한 마감 4H 봉 수 — 이보다 **많아야** 계산한다(러너와 같은 문턱)."""
        return max(
            self.ma_n or 0,
            self.band_n or 0,
            6 * ((self.surge_days or 0) + 2),
            0 if self.sma_bars is None else self.sma_bars + (self.sma_lag or 0) + 1,
            6 * ((self.vol_days or 0) + 5),
        )


def needs_of(playbooks: Iterable[Playbook]) -> RefNeeds:
    """세션에 실린 플레이북들이 선언한 국면값을 모은다.

    값마다 **처음 선언한 플레이북** 것을 쓴다(러너와 같은 규칙).

    Args:
        playbooks: 세션의 플레이북(세트면 여럿).

    Returns:
        필요한 값. 아무것도 안 선언했으면 `empty`.
    """
    books = tuple(playbooks)
    ma_n = next((b.entry_ref_ma_gate for b in books if b.entry_ref_ma_gate is not None), None)
    band = next(
        (b.entry_ref_return_band for b in books if b.entry_ref_return_band is not None), None
    )
    surge = next((b.entry_ref_surge_cap for b in books if b.entry_ref_surge_cap is not None), None)
    slope = next((b.entry_ref_sma_down for b in books if b.entry_ref_sma_down is not None), None)
    vol = next((b.entry_vol_target for b in books if b.entry_vol_target is not None), None)
    return RefNeeds(
        ma_n=ma_n,
        band_n=None if band is None else band.bars,
        surge_days=None if surge is None else surge.days,
        sma_bars=None if slope is None else slope.bars,
        sma_lag=None if slope is None else slope.lag,
        vol_days=None if vol is None else vol.days,
    )


@dataclass(frozen=True, slots=True)
class RefRegime:
    """국면값 한 벌. 필요 없던 값은 None(변동성은 빈 튜플).

    Attributes:
        above: 기준 종가가 SMA 위인가.
        ret: 띠 수익률.
        surge: 급등 수익률.
        sma_down: SMA 하락 중인가.
        vol: `(일봉이 끝난 시각, 연율 변동성)` 최근 3 일 치.
    """

    above: bool | None
    ret: Decimal | None
    surge: Decimal | None
    sma_down: bool | None
    vol: tuple[tuple[datetime, Decimal], ...]


def reference_regime(closed: Sequence[Candle], needs: RefNeeds) -> RefRegime:
    """마감된 기준 4H 봉에서 국면값을 낸다.

    Args:
        closed: **마감된** 기준 4H 봉(오름차순) — 진행 중인 봉은 부르는 쪽이 뺀다.
        needs: 필요한 값.

    Returns:
        국면값.

    Raises:
        ValueError: 봉이 `needs.bars` 이하 · 기준 종가 0 이하 · SMA 워밍업 미달 · 일봉 부족.
            러너는 이것을 "모름 = 문 잠금" 으로 받는다(규칙 #8-1 · 0.2.0 폴백).
    """
    if len(closed) <= needs.bars:
        raise ValueError(f"기준 캔들 부족: {len(closed)} <= {needs.bars}")
    above: bool | None = None
    ret: Decimal | None = None
    surge: Decimal | None = None
    sma_down: bool | None = None
    vol: tuple[tuple[datetime, Decimal], ...] = ()
    if needs.ma_n is not None:
        level = sma([c.close for c in closed], needs.ma_n)[-1]
        if level is None:
            raise ValueError("기준 SMA 워밍업 미달")
        above = closed[-1].close > level
    if needs.band_n is not None:
        before = closed[-1 - needs.band_n].close
        if before <= 0:
            raise ValueError("기준 종가가 0 이하다")
        ret = closed[-1].close / before - 1
    if needs.surge_days is not None:
        # UTC 일봉 종가 = 00:00 UTC 에 끝나는 4H 봉의 종가
        # (연구 `t296_wave113.btc_daily` 와 같은 값)
        daily = [c.close for c in closed if (c.ts + FOUR_HOURS).hour == 0]
        if len(daily) <= needs.surge_days or daily[-1 - needs.surge_days] <= 0:
            raise ValueError(f"기준 일봉 부족: {len(daily)} <= {needs.surge_days}")
        surge = daily[-1] / daily[-1 - needs.surge_days] - 1
    if needs.sma_bars is not None:
        # 연구 R2(`t296_wave104.btc_regimes`): 마감된 4H 종가 SMA 가 `lag` 봉 전보다 낮다
        line = sma([c.close for c in closed], needs.sma_bars)
        now_, then_ = line[-1], line[-1 - (needs.sma_lag or 0)]
        if now_ is None or then_ is None:
            raise ValueError("기준 SMA 워밍업 미달(하락 문)")
        sma_down = now_ < then_
    if needs.vol_days is not None:
        vol = btc_daily_vol(closed, needs.vol_days)
    return RefRegime(above=above, ret=ret, surge=surge, sma_down=sma_down, vol=vol)


def btc_daily_vol(
    bars: Sequence[Candle], days: int, keep: int = 3
) -> tuple[tuple[datetime, Decimal], ...]:
    """마감된 기준(BTC) 4H 봉에서 **UTC 일봉 연율 변동성**을 최근 `keep` 일 치 낸다.

    T304 · 변동성 목표.

    Args:
        bars: 마감된 4H 봉(오름차순).
        days: 로그 수익률 개수(30) — 일봉 종가 `days + 1` 개로 한 값을 낸다.
        keep: 돌려줄 최근 일수. 세션이 판정 봉 시작 시각까지 끝난 값을 고른다.

    Returns:
        `(그 일봉이 끝난 시각, 모집단 표준편차 x √365)` 오름차순.

    Raises:
        ValueError: 일봉이 `days + keep` 개보다 적거나 종가가 0 이하인 경우.

    Note:
        연구(`t296_wave115.setup`)와 같은 식이다 — 일봉 종가 = 00:00 UTC 에 끝나는 봉의 종가
        (`t296_wave113.btc_daily`) · `pstdev(rets[i-30:i]) x sqrt(365)`.
        float 로 계산해 Decimal 로 넘긴다.
    """
    daily = [(c.ts + FOUR_HOURS, float(c.close)) for c in bars if (c.ts + FOUR_HOURS).hour == 0]
    if len(daily) < days + keep or any(close <= 0 for _, close in daily):
        raise ValueError(f"기준 일봉 부족·이상: {len(daily)} < {days + keep}")
    out: list[tuple[datetime, Decimal]] = []
    for k in range(len(daily) - keep, len(daily)):
        window = [close for _, close in daily[k - days : k + 1]]
        rets = [math.log(after / before) for before, after in itertools.pairwise(window)]
        out.append((daily[k][0], Decimal(str(statistics.pstdev(rets) * math.sqrt(365)))))
    return tuple(out)
