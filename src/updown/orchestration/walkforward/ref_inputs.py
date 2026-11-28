"""기준(BTC) 국면값 · 시장 전체 값을 세션에 넣는 **한 길** — 실계좌 러너와 펀드 재현이 같이 부른다.

**책임**: 세션의 진입 문이 읽는 바깥 값(기준 국면값 · 시장 전체 값)을 "필요한 봉 수 ·
넣을 칸 · 모를 때 비울 칸 · 값의 날" 로 정한다. 값을 계산하는 식은
`analysis.indicators.reference` 에 있고, 값을 어디서 받는지(거래소 · DB · 과거 파일)는
부르는 쪽이 정한다.

까닭(T968 · 2026-11-21): 같은 일을 실계좌 러너와 펀드 재현이 따로 하다가 어긋났다 — 재현만 기준 봉을
600 개 고정으로 받아 1.49.0 변동성 순위(2,382 봉)를 못 셈 → 국면값이 통째로 "모름" → 진입 보류.
새 국면값 · 새 문이 생기면 이 모듈 한 곳만 고친다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from updown.analysis.indicators.reference import RefNeeds, RefRegime
    from updown.analysis.playbook.types import (
        BrakeRelease,
        Playbook,
        RankedBreadthGate,
        WashedRule,
    )

REF_MARGIN_BARS = 20
"""요구 봉 수 위에 더 받는 여유(실계좌 · 재현 같은 값)."""


def ref_warm_extra(needs: RefNeeds, playbooks: Iterable[Playbook]) -> int:
    """가상 보유(T811)용으로 기준 봉을 더 받는 수 — 변동성 백분위 문을 선언한 판만(보유 x 3 + 4).

    Args:
        needs: 국면값 필요.
        playbooks: 판의 매매법들.

    Returns:
        더 받을 봉 수(선언이 없으면 0).
    """
    if needs.vol_pct_bars is None:
        return 0
    return 3 * max((b.max_hold_bars or 0 for b in playbooks), default=0) + 4


def ref_fetch_bars(needs: RefNeeds, extra: int = 0) -> int:
    """받아야 할 기준 4H 봉 수 — 요구(`needs.bars`) + 여유 + 덧.

    Args:
        needs: 국면값 필요.
        extra: 더 받을 수(가상 보유 · 재현 앞머리).

    Returns:
        봉 수. 계산은 마감 봉이 `needs.bars` 보다 **많아야** 한다(같은 문턱).
    """
    return needs.bars + REF_MARGIN_BARS + extra


def apply_ref_regime(session: Any, needs: RefNeeds, got: RefRegime) -> None:
    """계산한 국면값을 세션 칸에 넣는다 — 선언한(필요한) 값만.

    Args:
        session: 세션(칸을 가진 것).
        needs: 국면값 필요.
        got: `reference_regime` 결과.
    """
    if needs.ma_n is not None:
        session.ref_above = got.above
    if needs.band_n is not None:
        session.ref_return = got.ret
    if needs.surge_days is not None:
        session.ref_surge = got.surge
    if needs.sma_bars is not None:
        session.ref_sma_down = got.sma_down
    if needs.vol_days is not None:
        session.ref_vol = got.vol
    if needs.vol_pct_bars is not None:
        session.ref_vol_pct = got.vol_pct
    if needs.pivot_k is not None:
        session.ref_pivot_s1 = got.pivot_s1
        session.ref_pivot_s4 = got.pivot_s4
    if needs.dist_days is not None:
        session.ref_dist_pct = got.dist_pct
    if needs.vol_off_days is not None:
        session.ref_vol_sd_pct = got.vol_sd_pct
    if needs.vol_rank_days is not None:
        session.ref_vol_rank = got.vol_rank
    if needs.lag_days is not None:
        session.ref_lag_ret = got.lag_ret
    if needs.washed_days is not None:
        session.ref_washed_ret = got.washed_ret


def clear_ref_regime(session: Any) -> None:
    """국면값을 전부 "모름" 으로 — 그 값을 읽는 문은 보류 · 배수는 기본값(규칙 #8-1).

    Args:
        session: 세션.
    """
    session.ref_above = None  # 모름 = 게이트 잠듦 (0.2.0 동작 폴백)
    session.ref_return = None  # 모름 = 국면 문 잠듦 (T290 · 같은 폴백)
    session.ref_surge = None  # 모름 = 급등 상한 보류 (T304 #8 · 같은 폴백)
    session.ref_sma_down = None  # 모름 = 하락 문 보류 (T304 #2)
    session.ref_vol = ()  # 모름 = 변동성 목표 크기 보류 (T304)
    session.ref_vol_pct = None  # 모름 = 변동성 백분위 문 보류 (T329)
    session.ref_pivot_s1 = session.ref_pivot_s4 = None  # 모름 = 배수 1 (T436)
    session.ref_dist_pct = None  # 모름 = 배수 1 (T783)
    session.ref_vol_sd_pct = None  # 모름 = 막지 않음 (T612 SV1)
    session.ref_vol_rank = None  # 모름 = 변동성 순위 문 보류
    session.ref_lag_ret = None  # 모름 = 뒤처짐 문 보류
    session.ref_washed_ret = None  # 모름 = 씻긴 시장 문 보류


def washed_rule_of(playbooks: Iterable[Playbook]) -> WashedRule | None:
    """씻긴 시장 문(`entry_washed`)을 처음 선언한 매매법의 선언 — 없으면 None(시장 값 안 넣음)."""
    return next((b.entry_washed for b in playbooks if b.entry_washed is not None), None)


def brake_release_of(playbooks: Iterable[Playbook]) -> BrakeRelease | None:
    """브레이크 풀기(`drawdown_brake.release`)를 처음 선언한 매매법의 선언 — 없으면 None (T985)."""
    return next(
        (
            b.drawdown_brake.release
            for b in playbooks
            if b.drawdown_brake is not None and b.drawdown_brake.release is not None
        ),
        None,
    )


def market_day_of(now: datetime) -> date:
    """시장 전체 값의 날 — `now` 기준 마지막으로 닫힌 UTC 날(그날 종가까지)."""
    return (now - timedelta(days=1)).date()


RANKED_DAYS_BACK = 2
"""순위 폭 줄을 며칠 치 넣나 — 판정 봉 시작이 든 날을 읽으므로(`Session.ranked_breadth_now`) 4H ·
1D 판정 봉(전날에 시작)도 덮게 `market_day_of(지금)` 과 그 앞 하루 · 이틀을 넣는다."""


def ranked_breadth_of(playbooks: Iterable[Playbook]) -> RankedBreadthGate | None:
    """순위 폭 문(`entry_ranked_breadth`)을 처음 선언한 매매법의 선언 — 없으면 None(값 안 넣음)."""
    # 재현 시험의 자리표(선언 칸 일부만 가진 것)도 받게 칸이 없으면 선언 없음으로 읽는다.
    found = (getattr(b, "entry_ranked_breadth", None) for b in playbooks)
    return next((rule for rule in found if rule is not None), None)


def ranked_days(now: datetime) -> tuple[date, ...]:
    """지금 넣을 순위 폭 줄의 날들(오래된 것부터) — 실계좌 러너 · 재현이 같은 날을 넣는다.

    Args:
        now: 지금(러너 = 벽시계 · 재현 = 시계 눈금).

    Returns:
        `market_day_of(now) - RANKED_DAYS_BACK` ~ `market_day_of(now)`.
    """
    last = market_day_of(now)
    return tuple(last - timedelta(days=k) for k in range(RANKED_DAYS_BACK, -1, -1))


def announcement_kinds_of(playbooks: Iterable[Playbook]) -> tuple[str, ...]:
    """공지 사건 다리(`entry_announcement`)가 읽는 공지 갈래들(정렬 · 고유) — 없으면 빈 튜플.

    실계좌 러너 · 재현이 같은 함수로 "이 판에 어느 갈래 사건을 넣나" 를 정한다(T1045 · T968 교훈).
    """
    found = (getattr(b, "entry_announcement", None) for b in playbooks)
    return tuple(sorted({rule.kind for rule in found if rule is not None}))


__all__ = [
    "RANKED_DAYS_BACK",
    "REF_MARGIN_BARS",
    "announcement_kinds_of",
    "apply_ref_regime",
    "clear_ref_regime",
    "market_day_of",
    "ranked_breadth_of",
    "ranked_days",
    "ref_fetch_bars",
    "ref_warm_extra",
    "washed_rule_of",
]
