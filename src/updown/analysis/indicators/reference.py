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
- `vol_sd_pct` (`entry_ref_vol_off.days` d): UTC 일봉 로그수익 d 개의 표본 표준편차 x 100
  (T612 SV1 · 숏 보호 · 연구 `t612_short_vol_dmul.sd20`).
- `vol_pct` (`entry_ref_vol_pct` bars · rank): 4H 로그수익 `bars` 개의 모집단 표준편차를 직전
  `rank` 개 값 안에서 평균 순위 백분위로 (T329 급락 되돌림 롱 · 연구 573 `BTC변동성백분위`).
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
        vol_pct_bars: `entry_ref_vol_pct.bars`(120 · 4H 로그수익 개수).
        vol_pct_rank: `entry_ref_vol_pct.rank`(500 · 백분위를 재는 창).
    """

    ma_n: int | None = None
    band_n: int | None = None
    surge_days: int | None = None
    sma_bars: int | None = None
    sma_lag: int | None = None
    vol_days: int | None = None
    vol_pct_bars: int | None = None
    vol_pct_rank: int | None = None
    pivot_k: int | None = None
    pivot_n: int | None = None
    pivot_lookback: int | None = None
    dist_days: int | None = None
    vol_off_days: int | None = None

    @property
    def empty(self) -> bool:
        """아무 값도 필요 없다 — 러너는 봉을 받으러 가지도 않는다."""
        return (
            self.ma_n is None
            and self.band_n is None
            and self.surge_days is None
            and self.sma_bars is None
            and self.vol_days is None
            and self.vol_pct_bars is None
            and self.pivot_k is None
            and self.dist_days is None
            and self.vol_off_days is None
        )

    @property
    def bars(self) -> int:
        """계산에 필요한 마감 4H 봉 수 — 이보다 **많아야** 계산한다(러너와 같은 문턱).

        전고점 국면(`pivot_k`)은 일봉 `lookback + 60 + 2k + n` 일 치 4H 봉을 쓴다 — 연구는 전체
        이력에서 사건을 찾았으므로 창을 넉넉히 잡아 창 밖 피벗이 빠지는 일을 줄인다
        (T558 패리티로 확인).
        """
        pivot_days = (
            0
            if self.pivot_k is None
            else (self.pivot_lookback or 60) + 60 + 2 * self.pivot_k + (self.pivot_n or 5)
        )
        return max(
            self.ma_n or 0,
            self.band_n or 0,
            6 * ((self.surge_days or 0) + 2),
            0 if self.sma_bars is None else self.sma_bars + (self.sma_lag or 0) + 1,
            6 * ((self.vol_days or 0) + 5),
            0 if self.vol_pct_bars is None else self.vol_pct_bars + (self.vol_pct_rank or 0) + 1,
            6 * pivot_days,
            6 * ((self.dist_days or 0) + 2),
            6 * ((self.vol_off_days or 0) + 3),
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
    vpct = next((b.entry_ref_vol_pct for b in books if b.entry_ref_vol_pct is not None), None)
    # 전고점 국면 배수(T436 · 혼합 4.3) — 피벗 · 되마감 · 창은 처음 선언한 플레이북 것
    # (다리들이 같은 값을 쓴다).
    reg = next(
        (b.entry_ref_regime_tilt for b in books if b.entry_ref_regime_tilt is not None), None
    )
    dist = next((b.entry_ref_dist_tilt for b in books if b.entry_ref_dist_tilt is not None), None)
    voff = next((b.entry_ref_vol_off for b in books if b.entry_ref_vol_off is not None), None)
    return RefNeeds(
        ma_n=ma_n,
        band_n=None if band is None else band.bars,
        surge_days=None if surge is None else surge.days,
        sma_bars=None if slope is None else slope.bars,
        sma_lag=None if slope is None else slope.lag,
        vol_days=None if vol is None else vol.days,
        vol_pct_bars=None if vpct is None else vpct.bars,
        vol_pct_rank=None if vpct is None else vpct.rank,
        pivot_k=None if reg is None else reg.pivot,
        pivot_n=None if reg is None else reg.confirm_days,
        pivot_lookback=None if reg is None else reg.lookback_days,
        dist_days=None if dist is None else dist.days,
        vol_off_days=None if voff is None else voff.days,
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
        vol_pct: 4H 실현변동성 백분위(0 ~ 1) — T329.
        pivot_s1: 전고점 국면 S1(안착 · 되밀림 · 없음) — T436 · 모르면 None.
        pivot_s4: 전고점 국면 S4(hold_up · fail_up · hold_dn · fail_dn) — 모르면 None.
    """

    above: bool | None
    ret: Decimal | None
    surge: Decimal | None
    sma_down: bool | None
    vol: tuple[tuple[datetime, Decimal], ...]
    vol_pct: Decimal | None = None
    pivot_s1: str | None = None
    pivot_s4: str | None = None
    dist_pct: Decimal | None = None
    vol_sd_pct: Decimal | None = None


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
    vol_pct: Decimal | None = None
    if needs.vol_pct_bars is not None:
        vol_pct = btc_vol_pct(closed, needs.vol_pct_bars, needs.vol_pct_rank or 500)
    s1: str | None = None
    s4: str | None = None
    if needs.pivot_k is not None:
        s1, s4 = pivot_states(closed, needs.pivot_k, needs.pivot_n or 5, needs.pivot_lookback or 60)
    dist_pct: Decimal | None = None
    if needs.dist_days is not None:
        # 연구 `BTC 200일 거리` = 닫힌 일봉 종가 ÷ 마지막 `days` 일 종가 평균 - 1 (그 날 포함)
        daily = [c.close for c in closed if (c.ts + FOUR_HOURS).hour == 0]
        if len(daily) < needs.dist_days:
            raise ValueError(f"기준 일봉 부족(SMA 거리): {len(daily)} < {needs.dist_days}")
        level = sum(daily[-needs.dist_days :], Decimal(0)) / needs.dist_days
        dist_pct = (daily[-1] / level - 1) * 100
    vol_sd_pct: Decimal | None = None
    if needs.vol_off_days is not None:
        # 연구 `t612_short_vol_dmul.sd20` = 닫힌 일봉 로그수익 마지막 `days` 개의
        # 표본 표준편차(ddof 1)
        closes = [float(c.close) for c in closed if (c.ts + FOUR_HOURS).hour == 0]
        if len(closes) < needs.vol_off_days + 1 or any(x <= 0 for x in closes):
            raise ValueError(
                f"기준 일봉 부족·이상(변동성 문): {len(closes)} < {needs.vol_off_days + 1}"
            )
        window = closes[-needs.vol_off_days - 1 :]
        rets = [math.log(b / a) for a, b in itertools.pairwise(window)]
        vol_sd_pct = Decimal(str(statistics.stdev(rets) * 100))
    return RefRegime(
        above=above,
        ret=ret,
        surge=surge,
        sma_down=sma_down,
        vol=vol,
        vol_pct=vol_pct,
        pivot_s1=s1,
        pivot_s4=s4,
        dist_pct=dist_pct,
        vol_sd_pct=vol_sd_pct,
    )


def daily_from_4h(bars: Sequence[Candle]) -> list[tuple[date, float, float, float, float]]:
    """마감된 4H 봉을 **완전한 UTC 일봉**(시 · 고 · 저 · 종)으로 묶는다 — 6 봉이 다 있는 날만.

    연구(`t380_structure_census.daily_bars` = 1H 를 1440 분으로 묶음)와 같은 고 · 저 · 종이다
    (고가의 최댓값 · 저가의 최솟값은 1H 로 묶든 4H 로 묶든 같다).
    """
    by_day: dict[date, list[Candle]] = {}
    for c in bars:
        by_day.setdefault(c.ts.date(), []).append(c)
    out: list[tuple[date, float, float, float, float]] = []
    for d in sorted(by_day):
        day = by_day[d]
        if len(day) < 6:
            continue
        out.append(
            (
                d,
                float(day[0].open),
                max(float(b.high) for b in day),
                min(float(b.low) for b in day),
                float(day[-1].close),
            )
        )
    return out


def pivot_events(
    daily: Sequence[tuple[date, float, float, float, float]], k: int, n: int
) -> list[tuple[date, str]]:
    """(상태에 들어가는 날, 사건) — 연구 `t380_structure_census.events` 그대로 (T436 · 혼합 4.3).

    피벗 고점 · 저점 = 양옆 `k` 일 안 최고 · 최저(k 일 지나 확정). 종가가 피벗을 넘으면 `n` 일 안에
    되마감(fail) 하는지 보고, 되마감하면 그날 · 안 하면 n 일째에 사건을 적는다.
    사건 날 = 결과 봉 **다음 날**.
    """
    highs = [x[2] for x in daily]
    lows = [x[3] for x in daily]
    closes = [x[4] for x in daily]
    piv_h: float | None = None
    piv_l: float | None = None
    used_h = used_l = False
    out: list[tuple[date, str]] = []
    one = timedelta(days=1)
    for t in range(len(daily)):
        j = t - k
        if j - k >= 0:
            if highs[j] == max(highs[j - k : j + k + 1]):
                piv_h, used_h = highs[j], False
            if lows[j] == min(lows[j - k : j + k + 1]):
                piv_l, used_l = lows[j], False
        c = closes[t]
        if piv_h is not None and not used_h and c > piv_h:
            used_h = True
            lvl, end = piv_h, min(len(daily) - 1, t + n)
            fail = next((m for m in range(t + 1, end + 1) if closes[m] < lvl), None)
            if fail is not None:
                out.append((daily[fail][0] + one, "fail_up"))
            elif t + n < len(daily):
                out.append((daily[end][0] + one, "hold_up"))
        if piv_l is not None and not used_l and c < piv_l:
            used_l = True
            lvl, end = piv_l, min(len(daily) - 1, t + n)
            fail = next((m for m in range(t + 1, end + 1) if closes[m] > lvl), None)
            if fail is not None:
                out.append((daily[fail][0] + one, "fail_dn"))
            elif t + n < len(daily):
                out.append((daily[end][0] + one, "hold_dn"))
    return sorted(out)


def pivot_states(
    closed: Sequence[Candle], k: int, n: int, lookback: int
) -> tuple[str | None, str | None]:
    """마감된 기준 4H 봉 → 오늘의 전고점 국면 S1 · S4 (T436 · 혼합 4.3).

    오늘 = 마지막 완전한 일봉의 다음 날.

    연구 `t436_regime_wf.states_n` 과 같은 식: 오늘 d 까지 날짜가 ≤ d 인 사건만 본다.
    - S1 = 최근 `lookback` 일 안 마지막 "_up" 사건이 fail_up 이면 "되밀림" · hold_up 이면 "안착" ·
      없으면 "없음".
    - S4 = 마지막 사건 이름 · 사건이 하나도 없으면 None(모름).

    Returns:
        `(s1, s4)` · 완전한 일봉이 `2k + n + 2` 개보다 적으면 둘 다 None.
    """
    daily = daily_from_4h(closed)
    if len(daily) < 2 * k + n + 2:
        return None, None
    today = daily[-1][0] + timedelta(days=1)
    ev = [e for e in pivot_events(daily, k, n) if e[0] <= today]
    ups = [e for e in ev if e[1].endswith("_up") and (today - e[0]).days <= lookback]
    s1 = "없음" if not ups else ("되밀림" if ups[-1][1] == "fail_up" else "안착")
    s4 = ev[-1][1] if ev else None
    return s1, s4


def btc_vol_pct(bars: Sequence[Candle], n: int, rank: int) -> Decimal:
    """마감된 기준(BTC) 4H 봉에서 **실현변동성 백분위**를 낸다 (T329 · 연구 573 `BTC변동성백분위`).

    Args:
        bars: 마감된 4H 봉(오름차순).
        n: 로그수익 개수(120) — 변동성 하나에 종가 `n + 1` 개.
        rank: 백분위 창(500) — 최근 `rank` 개 변동성 안에서 마지막 값의 평균 순위 ÷ rank.

    Returns:
        백분위(0 ~ 1).

    Raises:
        ValueError: 봉이 `n + rank` 개보다 적거나 종가가 0 이하인 경우.

    Note:
        연구 `lr.rolling(120).std(ddof=0)` → `.rolling(500).rank(pct=True)` 와 같은 값
        (pandas 평균 순위 · 동점은 평균 · 자기 포함). float 로 계산해 Decimal 로 넘긴다.
    """
    closes = [float(c.close) for c in bars]
    if len(closes) < n + rank or any(x <= 0 for x in closes):
        raise ValueError(f"기준 4H 봉 부족·이상: {len(closes)} < {n + rank}")
    rets = [math.log(after / before) for before, after in itertools.pairwise(closes)]
    vols = [statistics.pstdev(rets[k - n : k]) for k in range(len(rets) - rank + 1, len(rets) + 1)]
    last = vols[-1]
    below = sum(1 for v in vols if v < last)
    ties = sum(1 for v in vols if v == last)
    avg_rank = below + (ties + 1) / 2
    return Decimal(str(avg_rank / rank))


def btc_vol_pct_series(
    bars: Sequence[Candle], n: int, rank: int, last: int
) -> dict[datetime, Decimal]:
    """`btc_vol_pct` 를 **마지막 `last` 봉 각각**에 대해 낸다 — 봉 시작 시각 → 백분위 (T811).

    판이 새로 열릴 때 "연구라면 들고 있었을 몫" 을 거꾸로 걸으려면 지난 봉마다 그때의 문 값이
    필요하다. 봉마다 `btc_vol_pct` 를 부르면 (500 x 120) x `last` 번 계산이라 판 57 개가 한꺼번에
    열릴 때 이벤트 루프를 막는다 — 누적합으로 표준편차를 한 번에 내고 순위만 창마다 센다.

    Args:
        bars: 마감된 4H 봉(오름차순).
        n: 로그수익 개수(120).
        rank: 백분위 창(500).
        last: 낼 봉 수(마지막 마감 봉부터 거꾸로).

    Returns:
        `{봉 시작 시각: 백분위}` — 창이 모자란 봉은 빠진다(모름 = 부르는 쪽이 문 닫힘으로 읽는다).

    Note:
        모표준편차를 누적합(Σx · Σx²)으로 낸다 — `statistics.pstdev` 와 부동소수 끝자리만 다를 수
        있다(순위가 바뀌는 것은 같은 값 동점일 때뿐 · 판정 문의 마지막 값은 여전히 `btc_vol_pct`).
    """
    closes = [float(c.close) for c in bars]
    if len(closes) < n + 2 or any(x <= 0 for x in closes):
        return {}
    rets = [math.log(after / before) for before, after in itertools.pairwise(closes)]
    s1 = [0.0]
    s2 = [0.0]
    for r in rets:
        s1.append(s1[-1] + r)
        s2.append(s2[-1] + r * r)
    # vol[b] = 봉 b 까지의 로그수익 n 개(rets[b-n:b]) 모표준편차 — `btc_vol_pct` 의 k 와 같은 자리
    vol: dict[int, float] = {}
    for b in range(n, len(rets) + 1):
        mean = (s1[b] - s1[b - n]) / n
        vol[b] = math.sqrt(max(0.0, (s2[b] - s2[b - n]) / n - mean * mean))
    out: dict[datetime, Decimal] = {}
    for b in range(max(n + rank - 1, len(closes) - last), len(closes)):
        win = [vol[k] for k in range(b - rank + 1, b + 1)]
        here = vol[b]
        below = sum(1 for v in win if v < here)
        ties = sum(1 for v in win if v == here)
        out[bars[b].ts] = Decimal(str((below + (ties + 1) / 2) / rank))
    return out


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
