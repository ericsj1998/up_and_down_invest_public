"""**셀 성적** — 승률이 아니라 비대칭으로 잰다 (T153 §4 · 계획서 §1-3, §2-2).

## 🔴 승률은 성과가 아니다

2026-08-30 실측: 신호 하나 없이 동전만 던져 **승률 92.7%** 를 만들었고 합계는
**-3,915%** 였다 (익절 0.10% · 손절 없음). 지는 한 건이 이기는 한 건의 **37배**였다.

⇒ 주 판정 기준은 계획서 §2-2 의 **비대칭 점수**다:

    비대칭 = MFE 중앙값 / MAE 75백분위

먹을 수 있었던 폭이 견뎌야 했던 폭보다 큰가를 묻는다. 승률과 달리 **손절 위치를
바꿔도 잘 안 흔들린다** — 자리 자체의 성질이기 때문이다.

## ⭐ Gross 와 Net 을 **갈라서** 낸다 (계획서 §1-1)

    Gross +0.05% · 비용 0.13% → Net -0.08%
    ⇒ 이 신호는 **정보가 있다.** 단독 트리거로 못 쓸 뿐이다 (Tier B).

⚠️ 오늘 이 구분을 몰라 12판을 낭비했다. 전부 Net 음수라 "실패" 로 적었는데, Gross 를
갈라 보니 압축→확장만 +0.077% 였다.

## ⚠️ 되돌림은 **자리가 아니라 손절**의 문제다

계획서 §1-3 마지막 줄: *"무효화 후 되돌림이 높으면 = 손절 위치 문제"*. 손절 뒤에
원래 방향으로 돌아온 비율이 높다는 것은 자리가 나쁜 것이 아니라 **손절을 잘못 둔
것**이다. 이 둘을 구별 못 하면 좋은 자리를 버린다.

이 값은 여기서 계산하지 않는다 — 청산 **이후** 봉이 필요하고 그것은 스캔이 안다.
`Observation.reverted` 로 받는다.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from updown.orchestration.discovery.fill import Exit
from updown.orchestration.discovery.stats import (
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    Bootstrap,
    bootstrap_mean,
)

__all__ = [
    "Cell",
    "Observation",
    "percentile",
    "safety_margin",
    "summarise",
]

EARLY_BARS = 20
"""*"조기 판정"* 으로 볼 봉 수 (계획서 §1-3).

⚠️ **가정이다.** 진입 후 이만큼 안에 결말이 나면 조기 판정으로 센다. 값 자체보다
**셀끼리 비교**가 목적이라 같은 값을 모든 셀에 쓰는 것이 중요하다.
"""


def percentile(values: Sequence[float], fraction: float) -> float:
    """백분위 — 선형 보간 (표준 `inclusive` 방식).

    Args:
        values: 값들. 비어 있으면 안 된다.
        fraction: 0~1.

    Returns:
        백분위값.

    Raises:
        ValueError: 값이 없거나 `fraction` 이 범위 밖인 경우.

    Note:
        ⚠️ `statistics.quantiles` 는 표본이 2개 미만이면 예외다. 셀에 매매가 1건인
        경우가 실제로 생기므로(그런 셀은 표본 미달로 떨어지지만 **계산은 돌아야**
        한다) 직접 쓴다.
    """
    if not values:
        raise ValueError("값이 없다")
    if not 0 <= fraction <= 1:
        raise ValueError(f"백분위는 0~1 이다: {fraction}")
    ranked = sorted(values)
    if len(ranked) == 1:
        return ranked[0]
    position = fraction * (len(ranked) - 1)
    below = int(position)
    above = min(below + 1, len(ranked) - 1)
    weight = position - below
    return ranked[below] * (1 - weight) + ranked[above] * weight


@dataclass(frozen=True, slots=True)
class Observation:
    """매매 하나 — 셀 성적의 원재료.

    Attributes:
        day: 진입한 **날짜**(UTC). 부트스트랩의 재표집 단위다.
        gross_pct: **초과** 수익(%) — 무조건 평균을 뺀 값이다 (T153 §6-2).
        cost_pct: 왕복 비용(%). 부호는 양수(내는 돈).
        mae_pct: 최대 역행(%).
        mfe_pct: 최대 순행(%).
        exit: 어떻게 끝났나.
        bars: 보유 봉 수.
        drift_pct: 이 축·지평의 **무조건 평균 수익**(방향 반영). `gross_pct` 에서
            이미 빼 놓은 값이며, 더하면 원래 수익률이 복원된다.
        reverted: 손절 뒤 원래 방향으로 돌아왔나. 손절이 아니면 `None`.

    Note:
        🔴 **`gross_pct` 는 초과 수익이다** (T153 §6-2). 절대 수익을 쓰면 3일 보유
        롱이 무엇을 트리거로 쓰든 +0.85% 를 벌고, 실제로 무작위 진입이 Tier A 로
        떴다. 원래 값은 `gross_pct + drift_pct` 로 복원한다.

        🔴 `day` 가 성적표의 뼈대다. 매매를 개별로 세면 같은 날 같이 움직인 8종이
        독립인 것처럼 계산되고, 그러면 p 가 크게 작아진다 (`stats` 모듈).
    """

    day: date
    gross_pct: float
    cost_pct: float
    mae_pct: float
    mfe_pct: float
    exit: Exit
    bars: int
    drift_pct: float = 0.0
    reverted: bool | None = None

    @property
    def net_pct(self) -> float:
        """비용 차감 후 손익(%)."""
        return self.gross_pct - self.cost_pct


def safety_margin(gross_pct: float, cost_pct: float) -> float:
    """비용 대비 여유 배수 — **비용이 0 이하일 때를 조용히 넘기지 않는다**.

    Args:
        gross_pct: 비용 차감 전 손익(%).
        cost_pct: 왕복 비용(%). 펀딩을 받으면 **음수일 수 있다**.

    Returns:
        `gross / cost`. 비용이 0 이하면 부호에 따라 `+inf` 또는 `-inf`.

    Note:
        🔴 예전에는 비용이 0 이하이면 무조건 `+inf` 를 냈다. 그러면 **Gross 가
        음수인 칸이 마진 무한대**가 되어 `마진 >= 2.0` 관문을 그냥 통과한다.
        실측에서 실제로 그런 칸이 나왔다 (2026-08-31 · OSC-01 4h 롱 4320분:
        Gross **-0.5389%** · 비용 **-0.0086%** · 마진 `inf`).

        ⚠️ **음수 비용 자체는 정당하다.** 3일 보유면 펀딩 정산이 9번이고, 롱이
        받는 쪽이면 수수료(왕복 0.084%)를 넘길 수 있다. 고칠 것은 비용이 아니라
        *"그때 마진을 어떻게 부르는가"* 다.

        ⭐ 부호를 따라간다 — 비용을 안 내면서 버는 칸은 정말로 여유가 무한이고,
        비용을 안 내면서도 잃는 칸은 여유가 **없다**.
    """
    if cost_pct > 0:
        return gross_pct / cost_pct
    return float("inf") if gross_pct > 0 else float("-inf")


@dataclass(frozen=True, slots=True)
class Cell:
    """한 셀(신호 x TF x 방향)의 성적.

    Attributes:
        trades: 매매 수.
        days: 며칠에 걸쳐 있나. **Tier A 는 60일 이상을 요구한다.**
        gross_pct: Gross 평균(%).
        net_pct: Net 평균(%).
        cost_pct: 왕복 비용 평균(%).
        drift_pct: 이 칸이 뺀 **시장 드리프트** 평균(%). `gross_pct + drift_pct` 가
            원래(절대) 수익이다.
        margin: 안전마진 = Gross / 비용. **Tier A 는 2.0 이상.**
        asymmetry: MFE 중앙값 / MAE 75백분위.
        mfe_median: MFE 중앙값(%).
        mae_p75: MAE 75백분위(%).
        win_rate: 순손익 0 이상 비율. ⚠️ 참고용이지 판정 기준이 아니다.
        early_rate: `EARLY_BARS` 안에 끝난 비율.
        revert_rate: 손절 뒤 되돌아온 비율. 손절이 없으면 `None`.
        liquidations: 청산 건수. 🔴 **1 이상이면 하드 제약 위반**이다.
        exits: 결말별 건수.
        gross: Gross 평균의 부트스트랩.

    Note:
        ⚠️ `win_rate` 를 굳이 담는 이유는 **속지 않기 위해서**다. 승률 92.7% 에
        합계 -3,915% 인 표를 실제로 만들어 봤다 — 승률이 높은데 손익이 음수인 셀을
        눈으로 보게 두는 편이, 승률을 아예 안 보여 주는 것보다 낫다.
    """

    trades: int
    days: int
    gross_pct: float
    net_pct: float
    cost_pct: float
    drift_pct: float
    margin: float
    asymmetry: float
    mfe_median: float
    mae_p75: float
    win_rate: float
    early_rate: float
    revert_rate: float | None
    liquidations: int
    exits: dict[Exit, int]
    gross: Bootstrap

    @property
    def raw_gross_pct(self) -> float:
        """드리프트를 도로 더한 **절대** 수익(%) — 정보를 지우지 않으려고 남긴다."""
        return self.gross_pct + self.drift_pct

    @property
    def liquidation_free(self) -> bool:
        """하드 제약을 넘었나 — 청산이 **한 건도** 없어야 한다.

        Note:
            🔴 손익이 얼마든 청산이 있으면 그 셀은 탈락이다 (계획서 §0-2). 좋은
            성적으로 상쇄되지 않는다 — 계좌가 사라지면 그 뒤의 성적이 없다.
        """
        return self.liquidations == 0


def summarise(
    observations: Sequence[Observation],
    *,
    early_bars: int = EARLY_BARS,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
) -> Cell:
    """매매들을 셀 성적으로 접는다.

    Args:
        observations: 이 셀의 매매들.
        early_bars: 조기 판정으로 볼 봉 수.
        replicates: 부트스트랩 재표집 횟수.
        seed: 부트스트랩 씨앗. 고정이다 (절대 규칙 #5).

    Returns:
        성적.

    Raises:
        ValueError: 매매가 없는 경우. 빈 셀에 0 을 채워 돌려주면 표에서 *"성적이
            나빴다"* 와 *"매매가 없었다"* 가 구별되지 않는다.

    Note:
        🔴 Gross 에만 부트스트랩을 건다. 계획서 §1-1 이 **Gross 로 Tier 를 가른다** —
        Net 은 비용 구조가 바뀌면 같이 움직이지만 Gross 는 신호의 성질이다.

        ⚠️ 비용은 **평균**을 쓴다. 매매마다 다르므로(펀딩·야간·결말) 하나로 뭉치면
        정보가 준다 — 그래서 `cost_pct` 를 따로 남긴다.
    """
    if not observations:
        raise ValueError("매매가 없는 셀은 요약하지 않는다 — 0 으로 채우면 무성적과 구별이 안 된다")

    by_day: dict[date, list[float]] = {}
    for one in observations:
        by_day.setdefault(one.day, []).append(one.gross_pct)

    gross = bootstrap_mean(by_day, replicates=replicates, seed=seed)

    mfe = [one.mfe_pct for one in observations]
    mae = [one.mae_pct for one in observations]
    mfe_median = statistics.median(mfe)
    mae_p75 = percentile(mae, 0.75)

    gross_mean = sum(one.gross_pct for one in observations) / len(observations)
    cost_mean = sum(one.cost_pct for one in observations) / len(observations)
    drift_mean = sum(one.drift_pct for one in observations) / len(observations)

    stopped = [one for one in observations if one.exit is Exit.STOP and one.reverted is not None]
    exits: dict[Exit, int] = {}
    for one in observations:
        exits[one.exit] = exits.get(one.exit, 0) + 1

    return Cell(
        trades=len(observations),
        days=len(by_day),
        gross_pct=gross_mean,
        net_pct=gross_mean - cost_mean,
        cost_pct=cost_mean,
        drift_pct=drift_mean,
        # ⚠️ 비용이 0 이면 마진은 무한이다. 실제로는 안 생기지만(수수료가 있다),
        #    0 으로 나누어 죽는 것보다 무한을 내는 편이 표에서 눈에 띈다.
        margin=safety_margin(gross_mean, cost_mean),
        # ⚠️ MAE 75분위가 0 이면 역행이 거의 없었다는 뜻 — 비대칭은 무한이다.
        asymmetry=(mfe_median / mae_p75) if mae_p75 > 0 else float("inf"),
        mfe_median=mfe_median,
        mae_p75=mae_p75,
        win_rate=sum(1 for one in observations if one.net_pct >= 0) / len(observations),
        early_rate=sum(1 for one in observations if one.bars <= early_bars) / len(observations),
        revert_rate=(sum(1 for one in stopped if one.reverted) / len(stopped) if stopped else None),
        liquidations=sum(1 for one in observations if one.exit is Exit.LIQUIDATION),
        exits=exits,
        gross=gross,
    )
