"""**누산기** — 관측을 들고 있지 않고 셀 성적을 만든다 (T153 · 2026-08-30).

## 🔴 왜 필요했나 — 스캔이 스왑에서 죽었다

6.67년 격자를 돌리다 59분째 멈췄다. 실측:

    RSS 13.9GB + 스왑 3.3GB   물리 메모리는 15GB
    경과 59분 · CPU 시간 19분  → **40분을 스왑 대기**로 버렸다

원인은 `pooled` 가 신호 하나의 관측을 **8종 전부** 들고 있던 것이다. MA-04(20)은
5분봉에서 봉의 30% 에 터지므로:

    700,843봉 x 30% x 5지평 x 8종 = 841만 건 x 216B = **2.1GB** (신호 하나에)

여기에 8종의 봉·지표·캔들·스윙 캐시가 얹혀 물리 메모리를 넘었다.

## ⭐ 해법 — 관측을 **버리면서** 센다

셀 성적에 필요한 것은 관측 자체가 아니다:

    부트스트랩   날짜별 (합, 개수) 만 있으면 된다   → 2,433일이면 무시할 크기
    평균들       누적 합
    결말 분포    카운터
    MFE/MAE 분위수  ← 이것만 값이 필요하다

마지막 하나를 **히스토그램**으로 푼다. 저수지 표집이 아니라 히스토그램인 이유는
**난수가 안 들어가기 때문**이다 (절대 규칙 #5) — 씨앗을 고정해도 표집은 왜
그 값인지 설명하기 어렵고, 히스토그램은 결정적이다.

⇒ 메모리가 **관측 수와 무관**해진다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from updown.orchestration.discovery.fill import Exit
from updown.orchestration.discovery.metrics import Cell, Observation, safety_margin
from updown.orchestration.discovery.stats import (
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    bootstrap_daily,
)

__all__ = ["BINS", "Histogram", "Tally"]

BINS = 2000
LOW = 1e-4
HIGH = 1e3
"""히스토그램 격자 — 0.0001% ~ 1000% 를 로그로 나눈다.

⭐ 2,000칸이면 칸 하나가 **1.008배** 폭이라 분위수 오차가 0.8% 미만이다. MFE/MAE 는
비율(비대칭 점수)로 쓰이므로 그 정도면 충분하다.

⚠️ 로그 격자인 이유: 0.01% 와 0.02% 의 차이는 뜻이 있고 50% 와 50.01% 의 차이는
없다. 등간격으로 나누면 작은 쪽이 전부 한 칸에 뭉친다.
"""


@dataclass(slots=True)
class Histogram:
    """분위수만 필요한 값들의 누산기 — **값을 안 들고 있는다**.

    Attributes:
        counts: 칸별 개수.
        total: 전체 개수.
        zeros: 0 이하인 값의 개수. 로그 격자에 안 들어가므로 따로 센다.
    """

    counts: list[int] = field(default_factory=lambda: [0] * BINS)
    total: int = 0
    zeros: int = 0

    @classmethod
    def restore(cls, counts: Sequence[int], zeros: int) -> Histogram:
        """저장해 둔 칸 수에서 되돌린다.

        Args:
            counts: 칸별 개수 (`counts` 를 그대로 저장한 것).
            zeros: 0 이하였던 값의 개수.

        Returns:
            같은 분위수를 내는 누산기.

        Note:
            🔴 **`total` 을 여기서 다시 센다.** 되돌리는 코드를 스크립트마다 다시
            적었더니 한 곳이 `total` 을 안 채웠고, 그러면 `percentile` 이 조용히
            **전부 0** 을 냈다 — 손절·익절이 0 이 되어 매매가 한 건도 안 열렸다.
            예외도 경고도 없었다. 그래서 되돌리는 법을 여기 하나로 둔다.
        """
        filled = list(counts) + [0] * (BINS - len(counts))
        return cls(counts=filled, total=sum(filled) + zeros, zeros=zeros)

    def add(self, value: float) -> None:
        """값 하나를 넣는다.

        Args:
            value: 백분율 값 (음수면 0 으로 본다).
        """
        self.total += 1
        if value <= 0:
            self.zeros += 1
            return
        self.counts[_bin(value)] += 1

    def merge(self, other: Histogram) -> None:
        """다른 누산기를 흡수한다 — 종목별로 세고 합칠 때 쓴다.

        Args:
            other: 흡수할 누산기.
        """
        for index, count in enumerate(other.counts):
            self.counts[index] += count
        self.total += other.total
        self.zeros += other.zeros

    def percentile(self, fraction: float) -> float:
        """분위수.

        Args:
            fraction: 0~1.

        Returns:
            그 분위의 값. 관측이 없으면 0.

        Raises:
            ValueError: `fraction` 이 범위 밖.

        Note:
            ⚠️ 칸 **아래 경계**를 돌려준다 (보간하지 않는다). 칸 폭이 0.8% 라
            보간해도 차이가 그 안이고, 안 하면 규칙이 단순해진다.
        """
        if not 0 <= fraction <= 1:
            raise ValueError(f"분위수는 0~1 이다: {fraction}")
        if self.total == 0:
            return 0.0
        target = fraction * self.total
        if target <= self.zeros:
            return 0.0
        seen = self.zeros
        for index, count in enumerate(self.counts):
            seen += count
            if seen >= target:
                return _edge(index)
        return _edge(BINS - 1)


def _bin(value: float) -> int:
    """값이 들어갈 칸 번호."""
    spread = math.log(HIGH / LOW)
    place = int(math.log(max(value, LOW) / LOW) / spread * BINS)
    return min(max(place, 0), BINS - 1)


def _edge(index: int) -> float:
    """칸의 아래 경계값."""
    spread = math.log(HIGH / LOW)
    return LOW * math.exp(index / BINS * spread)


@dataclass(slots=True)
class Tally:
    """한 칸(신호 x 축 x 방향 x 지평)의 누산기.

    Note:
        🔴 관측을 **저장하지 않는다.** 841만 건짜리 칸이 실제로 있었고 그것이
        2.1GB 였다 (모듈 docstring). 여기서는 날짜 수 x 상수만 든다.
    """

    days: dict[date, list[float]] = field(default_factory=dict[date, list[float]])
    trades: int = 0
    gross_sum: float = 0.0
    cost_sum: float = 0.0
    drift_sum: float = 0.0
    wins: int = 0
    early: int = 0
    stopped: int = 0
    reverted: int = 0
    exits: dict[Exit, int] = field(default_factory=dict[Exit, int])
    mfe: Histogram = field(default_factory=Histogram)
    mae: Histogram = field(default_factory=Histogram)
    mae_days: dict[date, list[float]] = field(default_factory=dict[date, list[float]])
    """날짜 → [MAE 합, 건수].

    🔴 **히스토그램만으로는 신뢰구간을 못 낸다** — 분위수는 나오는데 재표집이
    안 된다. MAE 개선이 유의한지 물으려면 날짜별 원장이 있어야 하고, 그것을
    안 저장해서 한 번 답을 못 냈다 (2026-08-31).

    ⚠️ Gross 원장과 **같은 해상도·같은 가중**이다. 매매 가중으로 재려면
    (합, 건수) 두 개가 필요하다 — 날짜별 평균만 두면 하루 건수가 다른 두 쪽을
    같은 무게로 세게 되고, 그 결함을 이미 한 번 겪었다.
    """
    by_regime: dict[str, list[float]] = field(default_factory=dict[str, list[float]])
    """국면 → [수익 합, 건수]. Tier A 조건인 **전 국면 부호 일치**가 이것을 쓴다.

    🔴 국면별로 따로 누산기를 두지 않는다. 칸이 2,000개인데 4배로 늘리면 열쇠가
    복잡해지고, 필요한 것은 **부호가 갈리는지**뿐이라 (합, 건수) 두 개면 된다.

    ⚠️ 국면은 **날짜만의 함수**여야 여기 담을 수 있다 (`regime` 모듈 — BTC 200일선).
    종목별 국면을 쓰려면 스캔을 다시 돌아야 한다.
    """

    def add(self, one: Observation, *, early_bars: int, regime: str = "") -> None:
        """관측 하나를 세고 **버린다**.

        Args:
            one: 관측.
            early_bars: 조기 판정으로 볼 봉 수.
            regime: 진입일의 국면 ( 의 값). 빈 문자열이면 안 센다.
        """
        bucket = self.days.get(one.day)
        if bucket is None:
            # [합, 개수] — 부트스트랩이 요구하는 전부다.
            self.days[one.day] = [one.gross_pct, 1.0]
        else:
            bucket[0] += one.gross_pct
            bucket[1] += 1.0
        self.trades += 1
        self.gross_sum += one.gross_pct
        self.cost_sum += one.cost_pct
        self.drift_sum += one.drift_pct
        self.wins += one.net_pct >= 0
        self.early += one.bars <= early_bars
        self.exits[one.exit] = self.exits.get(one.exit, 0) + 1
        if regime:
            bucket = self.by_regime.setdefault(regime, [0.0, 0.0])
            bucket[0] += one.gross_pct - one.cost_pct
            bucket[1] += 1

        self.mfe.add(one.mfe_pct)
        bucket = self.mae_days.get(one.day)
        if bucket is None:
            self.mae_days[one.day] = [one.mae_pct, 1.0]
        else:
            bucket[0] += one.mae_pct
            bucket[1] += 1.0
        self.mae.add(one.mae_pct)
        if one.exit is Exit.STOP and one.reverted is not None:
            self.stopped += 1
            self.reverted += one.reverted

    def cell(self, *, replicates: int = DEFAULT_REPLICATES, seed: int = DEFAULT_SEED) -> Cell:
        """성적으로 접는다.

        Args:
            replicates: 부트스트랩 재표집 횟수.
            seed: 씨앗.

        Returns:
            셀 성적.

        Raises:
            ValueError: 관측이 없거나 날짜가 하나뿐인 경우.

        Note:
            ⭐ 부트스트랩에 **날짜별 (합, 개수)** 를 그대로 넘긴다 (`bootstrap_daily`).
            관측을 복원해 넘기면 이 클래스가 존재하는 이유가 사라진다.
        """
        if not self.trades:
            raise ValueError("매매가 없는 칸은 요약하지 않는다")
        ordered = sorted(self.days)
        gross = bootstrap_daily(
            [self.days[day][0] for day in ordered],
            [int(self.days[day][1]) for day in ordered],
            replicates=replicates,
            seed=seed,
        )

        gross_mean = self.gross_sum / self.trades
        cost_mean = self.cost_sum / self.trades
        drift_mean = self.drift_sum / self.trades
        mfe_median = self.mfe.percentile(0.5)
        mae_p75 = self.mae.percentile(0.75)
        return Cell(
            trades=self.trades,
            days=len(self.days),
            gross_pct=gross_mean,
            net_pct=gross_mean - cost_mean,
            cost_pct=cost_mean,
            drift_pct=drift_mean,
            margin=safety_margin(gross_mean, cost_mean),
            asymmetry=(mfe_median / mae_p75) if mae_p75 > 0 else float("inf"),
            mfe_median=mfe_median,
            mae_p75=mae_p75,
            win_rate=self.wins / self.trades,
            early_rate=self.early / self.trades,
            revert_rate=(self.reverted / self.stopped) if self.stopped else None,
            liquidations=self.exits.get(Exit.LIQUIDATION, 0),
            exits=dict(self.exits),
            gross=gross,
        )


def fold(observations: Iterable[Observation], *, early_bars: int) -> Tally:
    """관측들을 누산기로 접는다 — 시험용 편의.

    Args:
        observations: 관측들.
        early_bars: 조기 판정 봉 수.

    Returns:
        누산기.
    """
    tally = Tally()
    for one in observations:
        tally.add(one, early_bars=early_bars)
    return tally
