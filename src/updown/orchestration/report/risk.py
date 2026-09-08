"""위험 등급 — MDD · 청산률 · 수면 아래로 공격적/균형/안전을 가른다 (T231 · 사용자 2026-09-09).

> *"추천도 3가지 — 공격적 · 균형 · 안전 투자 추천. MDD 와 청산률, 수면 아래 기준으로."*

등급은 **숫자가** 정한다 — 채택("추천")은 사람이 `playbooks.yml` 에 적고, 등급은 저장소의 자본
곡선·매매에서 계산한다 (절대 규칙 #12 "권위가 아니라 성과가 판정"). 문턱은 아래 상수 하나에
모아 둔다 — 사용자가 숫자를 주면 여기만 바뀐다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Tier = Literal["safe", "balanced", "aggressive"]

TIER_LABELS: dict[Tier, str] = {
    "safe": "안전 투자",
    "balanced": "균형 투자",
    "aggressive": "공격적 투자",
}


@dataclass(frozen=True, slots=True)
class TierRule:
    """등급 하나의 상한 — 셋을 **모두** 만족해야 그 등급이다.

    Attributes:
        mdd_pct: 최대 낙폭 상한 (%).
        liquidation_rate_pct: 청산률 상한 (% · 청산 건수 ÷ 매매 건수).
        underwater_pct: 수면 아래 비율 상한 (% · 자본이 직전 고점 아래였던 봉 비율).
    """

    mdd_pct: float
    liquidation_rate_pct: float
    underwater_pct: float


#: ⚠️ **결정 필요 (T231 · 기본값은 제안)** — 안전 = MDD 25% · 청산 0 · 수면 50% /
#: 균형 = MDD 50% · 청산률 0.5% · 수면 70%.
TIER_RULES: dict[Tier, TierRule] = {
    "safe": TierRule(mdd_pct=25.0, liquidation_rate_pct=0.0, underwater_pct=50.0),
    "balanced": TierRule(mdd_pct=50.0, liquidation_rate_pct=0.5, underwater_pct=70.0),
}


def underwater_pct(values: Sequence[float]) -> float:
    """자본 곡선이 직전 고점 **아래**에 있던 점의 비율 (%) — "수면 아래".

    Args:
        values: 시각 순 자본(또는 배수). 빈 목록은 0.

    Returns:
        0~100. 첫 점은 고점이라 세지 않는다. 고점과 같은 점은 수면 위다.
    """
    if len(values) < 2:
        return 0.0
    peak = values[0]
    under = 0
    for v in values[1:]:
        if v < peak:
            under += 1
        else:
            peak = v
    return round(under / (len(values) - 1) * 100, 2)


def max_drawdown_pct(values: Sequence[float]) -> float:
    """자본 곡선의 최대 낙폭 (%) — 직전 고점 대비.

    Args:
        values: 시각 순 자본. 0 이하의 고점은 건너뛴다.

    Returns:
        0~100.
    """
    peak = 0.0
    deepest = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            deepest = max(deepest, (peak - v) / peak * 100)
    return round(deepest, 2)


def liquidation_rate_pct(liquidations: int, trades: int) -> float:
    """청산률 (%) — 매매가 없으면 0.

    Args:
        liquidations: 강제청산 건수.
        trades: 마감된 매매 건수.

    Returns:
        0~100.
    """
    if trades <= 0:
        return 0.0
    return round(liquidations / trades * 100, 3)


def risk_tier(*, mdd_pct: float, liquidation_rate_pct: float, underwater_pct: float) -> Tier:
    """세 지표로 등급을 정한다 — 안전 → 균형 순으로 맞춰 보고, 둘 다 아니면 공격적.

    Args:
        mdd_pct: 최대 낙폭 (%).
        liquidation_rate_pct: 청산률 (%).
        underwater_pct: 수면 아래 비율 (%).

    Returns:
        `safe` · `balanced` · `aggressive`.
    """
    for tier in ("safe", "balanced"):
        rule = TIER_RULES[tier]
        if (
            mdd_pct <= rule.mdd_pct
            and liquidation_rate_pct <= rule.liquidation_rate_pct
            and underwater_pct <= rule.underwater_pct
        ):
            return tier
    return "aggressive"


__all__ = [
    "TIER_LABELS",
    "TIER_RULES",
    "Tier",
    "TierRule",
    "liquidation_rate_pct",
    "max_drawdown_pct",
    "risk_tier",
    "underwater_pct",
]
