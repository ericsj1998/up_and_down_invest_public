"""온보딩 위저드의 산수 — 성향 → 기본 매매법 · 과거 창 실측 (T247 · 순수).

"예상" 이라는 말은 없다. 숫자는 전부 세션 엔진 저장소(`config/evidence/backtest_*.json`)의
**과거 창 실측**이고,
창이 저장소 구간보다 길면 그 창은 None 이다 — 짧은 구간을 3년이라 부르지 않는다 (규칙 #8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from updown.orchestration.report.risk import Tier, max_drawdown_pct, underwater_pct

WINDOWS: dict[str, int] = {"3y": 1095, "2y": 730, "1y": 365, "1m": 30}
"""대시보드 창(일). 1일 창은 일부러 없다 — 하루 값은 잡음이라 사람을 오해시킨다 (T247 ⭐)."""

COVERAGE_SLACK = 0.03
"""저장소 구간이 창보다 이만큼(비율) 짧아도 그 창으로 친다 — 4h 봉 축의 끝 오차."""

_DAY = 86_400


@dataclass(frozen=True, slots=True)
class WindowStats:
    """한 창의 실측.

    Attributes:
        days: 창 길이(일).
        total_pct: 창 시작 대비 손익 %.
        mdd_pct: 창 안 최대 낙폭 %.
        underwater_pct: 창 안 수면 아래 비율 %.
        trades: 창 안에 닫힌 매매 수.
        liquidations: 그중 청산.
    """

    days: int
    total_pct: float
    mdd_pct: float
    underwater_pct: float
    trades: int
    liquidations: int


def window_stats(
    ts: Sequence[int],
    values: Sequence[float],
    trades: Sequence[dict[str, Any]],
    *,
    days: int,
) -> WindowStats | None:
    """자본 곡선의 끝에서 `days` 일 창을 잘라 잰다.

    Args:
        ts: 곡선 시각(epoch 초 · 오름차순).
        values: 곡선 값.
        trades: 매매 행(`closed_ts` · `reason`).
        days: 창 길이.

    Returns:
        실측. 곡선이 창보다 짧으면 None.
    """
    if len(ts) < 2 or len(ts) != len(values):
        return None
    end = ts[-1]
    start = end - days * _DAY
    covered = (end - ts[0]) / (days * _DAY)
    if covered < 1 - COVERAGE_SLACK:
        return None
    index = next((i for i, t in enumerate(ts) if t >= start), 0)
    slice_values = list(values[index:])
    base = slice_values[0]
    if base <= 0:
        return None
    closed = [t for t in trades if int(t.get("closed_ts") or 0) >= start]
    return WindowStats(
        days=days,
        total_pct=round((slice_values[-1] / base - 1) * 100, 2),
        mdd_pct=round(max_drawdown_pct(slice_values), 2),
        underwater_pct=round(underwater_pct(slice_values), 1),
        trades=len(closed),
        liquidations=sum(1 for t in closed if str(t.get("reason")) == "liq"),
    )


TierRule = Literal["max_total", "max_calmar", "min_mdd"]
TIER_PICK: dict[Tier, TierRule] = {
    "aggressive": "max_total",
    "balanced": "max_calmar",
    "safe": "min_mdd",
}
"""성향 → 고르는 규칙 (T247 결정 1 · 권장안): 공격적 = 손익 최대 · 균형 = Calmar 최대 ·
안전 = MDD(동률이면 수면) 최소."""


def pick_default(tier: Tier, candidates: Sequence[dict[str, Any]]) -> str | None:
    """성향에 맞는 기본 매매법 id.

    Args:
        tier: 성향.
        candidates: 후보 요약들 — `id` · `total_pct` · `calmar` · `mdd_pct` · `underwater_pct`
            (없으면 None).

    Returns:
        고른 id. 잴 수 있는 후보가 없으면 None — 조용히 첫 것을 주지 않는다.
    """
    rule = TIER_PICK[tier]
    usable = [c for c in candidates if isinstance(c.get("id"), str)]
    if rule == "max_total":
        scored = [(float(c["total_pct"]), c) for c in usable if c.get("total_pct") is not None]
        return max(scored, key=lambda p: p[0])[1]["id"] if scored else None
    if rule == "max_calmar":
        scored = [(float(c["calmar"]), c) for c in usable if c.get("calmar") is not None]
        return max(scored, key=lambda p: p[0])[1]["id"] if scored else None
    scored_low = [
        (float(c["mdd_pct"]), float(c.get("underwater_pct") or 0), c)
        for c in usable
        if c.get("mdd_pct") is not None
    ]
    return min(scored_low, key=lambda p: (p[0], p[1]))[2]["id"] if scored_low else None


__all__ = ["COVERAGE_SLACK", "TIER_PICK", "WINDOWS", "WindowStats", "pick_default", "window_stats"]
