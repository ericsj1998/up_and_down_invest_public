"""**손절·목표를 분포에서 역산한다** (T154 §3 · 계획서 §2-1).

## ⛔ 손절폭을 손으로 정하지 않는다

T154 §3 이 명시한다. 예전에 `MIN_STOP_PCT = 0.5%` 를 실측에서 뽑아 썼는데, 그것은
*"이 아래는 노이즈에 죽는다"* 는 관찰이었지 **MAE 분포에서 역산한 값이 아니었다**.

    손절 폭      MAE 분포 **70~80 백분위**   더 타이트하면 이길 매매도 잘린다
    초기 목표    MFE 분포 **중앙값** 근처
    조기 축소    MAE 분포 **40~50 백분위**
    시간 청산    **조기 판정율이 꺾이는 지점**
    보유 상한    **없음이 기본** — 두려면 근거를 데이터에서

## 🔴 왜 70~80 백분위인가

손절이 MAE 중앙값이면 **절반의 매매가 잘린다** — 그중에는 결국 이겼을 것도 있다.
75 백분위면 네 번 중 세 번은 살아남는다. 100 백분위(최대 역행)로 두면 안 잘리지만
그때는 손절이 청산보다 멀어져 **손절이 장식**이 된다 (`liquidation.judge`).

⚠️ 이 값은 **셀마다 다르다.** 신호마다 견뎌야 하는 폭이 다르기 때문이고, 그것이
분포에서 역산하는 이유다.

## ⚠️ 지평은 **결과**지 입력이 아니다 (계획서 §1-4)

시간 청산은 *"조기 판정율이 꺾이는 지점"* 으로 정한다. 지평별 조기 판정율이 늘다가
평평해지면 그 뒤로는 기다려도 결과가 안 갈린다는 뜻이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = ["Levels", "derive", "time_exit"]

STOP_PERCENTILE = 0.75
TARGET_PERCENTILE = 0.50
TRIM_PERCENTILE = 0.45
"""계획서 §2-1 이 준 범위의 가운데.

⚠️ 범위(70~80 · 40~50)의 가운데를 쓴다. 안에서 어디를 고를지는 Stage 6 의 **고원
확인**이 답할 문제이지 지금 고를 것이 아니다.
"""


@dataclass(frozen=True, slots=True)
class Levels:
    """한 셀에서 역산한 값들 — 전부 **진입가 대비 %**.

    Attributes:
        stop_pct: 손절 폭. MAE 75 백분위.
        target_pct: 초기 목표. MFE 중앙값.
        trim_pct: 조기 축소 임계. MAE 45 백분위.
        reward_risk: 목표 / 손절. 1 미만이면 **한 번 져서 한 번으로 못 갚는다**.

    Note:
        🔴 `reward_risk` 가 낮으면 필요 승률이 올라간다. 그 산수는
        `analysis.plan.required_win_rate` 가 하며, 여기서 다시 만들지 않는다.
    """

    stop_pct: float
    target_pct: float
    trim_pct: float

    @property
    def reward_risk(self) -> float:
        """목표 / 손절. 손절이 0 이면 무한이다."""
        return (self.target_pct / self.stop_pct) if self.stop_pct > 0 else float("inf")


def derive(
    mae: Sequence[float] | None = None,
    mfe: Sequence[float] | None = None,
    *,
    mae_at: Mapping[float, float] | None = None,
    mfe_at: Mapping[float, float] | None = None,
) -> Levels:
    """MAE·MFE 분포에서 손절·목표를 역산한다.

    Args:
        mae: 최대 역행 값들 (%). `mae_at` 을 주면 무시된다.
        mfe: 최대 순행 값들 (%).
        mae_at: 이미 계산된 MAE 분위수 (백분위 → 값). 히스토그램이 있을 때 쓴다.
        mfe_at: 이미 계산된 MFE 분위수.

    Returns:
        역산된 값들.

    Raises:
        ValueError: 분포도 분위수도 안 준 경우.

    Note:
        ⭐ 값 목록과 분위수 표 **둘 다** 받는다. 스캔은 히스토그램만 들고 있고
        (`tally.Histogram`), 시험은 값을 직접 주는 편이 읽기 좋다.
    """
    if mae_at is None:
        if mae is None:
            raise ValueError("MAE 분포나 분위수 중 하나는 있어야 한다")
        mae_at = {
            STOP_PERCENTILE: _quantile(mae, STOP_PERCENTILE),
            TRIM_PERCENTILE: _quantile(mae, TRIM_PERCENTILE),
        }
    if mfe_at is None:
        if mfe is None:
            raise ValueError("MFE 분포나 분위수 중 하나는 있어야 한다")
        mfe_at = {TARGET_PERCENTILE: _quantile(mfe, TARGET_PERCENTILE)}

    return Levels(
        stop_pct=mae_at[STOP_PERCENTILE],
        target_pct=mfe_at[TARGET_PERCENTILE],
        trim_pct=mae_at[TRIM_PERCENTILE],
    )


def _quantile(values: Sequence[float], fraction: float) -> float:
    """백분위 — 선형 보간."""
    if not values:
        raise ValueError("값이 없다")
    ranked = sorted(values)
    if len(ranked) == 1:
        return ranked[0]
    place = fraction * (len(ranked) - 1)
    below = int(place)
    above = min(below + 1, len(ranked) - 1)
    weight = place - below
    return ranked[below] * (1 - weight) + ranked[above] * weight


def time_exit(early_by_horizon: Mapping[int, float], *, slack: float = 0.02) -> int | None:
    """**조기 판정율이 꺾이는 지점** — 시간 청산 후보 (계획서 §2-1).

    Args:
        early_by_horizon: 지평(분) → 조기 판정율.
        slack: 이만큼 미만으로 늘면 *"꺾였다"* 로 본다.

    Returns:
        그 지평(분). 끝까지 꺾이지 않으면 `None` — **상한을 두지 않는다**.

    Note:
        🔴 판정율이 늘다가 평평해지면 그 뒤로는 **기다려도 결과가 안 갈린다.**
        거기가 시간 청산 자리다.

        ⛔ 끝까지 오르면 `None` 을 낸다. 억지로 마지막 지평을 고르면 *"데이터가
        여기서 끝났다"* 를 *"여기가 최적이다"* 로 바꿔 적는 것이다 (계획서 §2-1:
        보유 상한은 **없음이 기본**).
    """
    ordered = sorted(early_by_horizon)
    for before, after in pairwise(ordered):
        if early_by_horizon[after] - early_by_horizon[before] < slack:
            return before
    return None
