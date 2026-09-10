"""자동 실행 모드의 문 — 순수 (T248 · 사용자 확정 2026-09-09).

"다음에는 묻지 않음" 을 켠 사람의 제안은 RiskManager 확정값으로 **확인 없이** 주문된다. 그 문은
셋이다: 동의(문구 버전) · 일 최대 건수 · 총자본 대비 노출 상한. 문턱은 설정이고 기본값은 사용자
확정(3건 · 30%).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_MAX_PER_DAY = 3
DEFAULT_MAX_EXPOSURE_PCT = Decimal(30)


@dataclass(frozen=True, slots=True)
class AutoState:
    """사람 하나의 자동 모드 상태.

    Attributes:
        enabled: 켰나.
        consent_version: 동의한 문구 버전. None 이면 동의 없음.
        shares: 주식 기본 주수.
        margin: 코인 기본 예산(USDT).
        max_per_day: 일 최대 건수.
        max_exposure_pct: 총자본 대비 노출 상한(%).
    """

    enabled: bool
    consent_version: str | None
    shares: int = 1
    margin: Decimal = Decimal(50)
    max_per_day: int = DEFAULT_MAX_PER_DAY
    max_exposure_pct: Decimal = DEFAULT_MAX_EXPOSURE_PCT


@dataclass(frozen=True, slots=True)
class AutoVerdict:
    """자동 발주 가능 여부.

    Attributes:
        ok: 낼 수 있나.
        why: 못 내는 이유 (낼 수 있으면 빈 문자열).
    """

    ok: bool
    why: str = ""


def auto_allowed(
    state: AutoState,
    *,
    current_version: str,
    placed_today: int,
    exposure_now: Decimal,
    new_margin: Decimal,
    total: Decimal,
    proposal_ok: bool,
    market_allowed: bool,
    playbook_allowed: bool,
) -> AutoVerdict:
    """자동 발주의 문 — 전부 통과해야 한다.

    Args:
        state: 사람의 자동 모드 상태.
        current_version: 지금 동의 문구 버전.
        placed_today: 오늘 이미 낸 AI 주문 수.
        exposure_now: 지금 AI 판들에 잡힌 예산 합.
        new_margin: 이번 주문 예산.
        total: 계정 총액.
        proposal_ok: RiskManager 가 막지 않았나.
        market_allowed: 그 시장 거래 권한.
        playbook_allowed: 그 매매법 권한.

    Returns:
        판정. 이유는 사람에게 그대로 보인다.
    """
    if not state.enabled:
        return AutoVerdict(False, "자동 모드가 꺼져 있다")
    if state.consent_version != current_version:
        return AutoVerdict(False, "자동 주문 동의가 없거나 문구가 바뀌었다 — 다시 동의한다")
    if not proposal_ok:
        return AutoVerdict(False, "RiskManager 가 막은 제안이다")
    if not market_allowed or not playbook_allowed:
        return AutoVerdict(False, "권한 없는 시장·매매법이다")
    if placed_today >= state.max_per_day:
        return AutoVerdict(False, f"오늘 자동 주문 {placed_today}건 — 상한 {state.max_per_day}")
    if total <= 0:
        return AutoVerdict(False, "계정 총액을 모른다 — 노출 상한을 못 잰다")
    exposure_pct = (exposure_now + new_margin) / total * 100
    if exposure_pct > state.max_exposure_pct:
        return AutoVerdict(
            False,
            f"노출 {exposure_pct:.1f}% > 상한 {state.max_exposure_pct}% "
            f"(지금 {exposure_now} + 이번 {new_margin} / {total})",
        )
    return AutoVerdict(True)


__all__ = [
    "DEFAULT_MAX_EXPOSURE_PCT",
    "DEFAULT_MAX_PER_DAY",
    "AutoState",
    "AutoVerdict",
    "auto_allowed",
]


DEFAULT_MARGIN = Decimal(50)
"""코인 기본 예산(USDT) — 설정 이벤트에 없을 때."""
DEFAULT_SHARES = 1
"""주식 기본 주수 — 설정 이벤트에 없을 때."""


def state_from_event(found: Mapping[str, object] | None) -> AutoState:
    """마지막 `ai_auto_mode` 이벤트 → 상태 (T269 #8 · 판정 입력은 orchestration 이 만든다).

    Args:
        found: 이벤트 payload. None 이면 켠 적 없음.

    Returns:
        상태 — 없는 칸은 기본값(일 3건 · 30% · 예산 50 · 1주).
    """
    row = dict(found or {})
    return AutoState(
        enabled=bool(row.get("enabled")),
        consent_version=None if row.get("consent_version") is None else str(row["consent_version"]),
        shares=int(str(row.get("shares") or DEFAULT_SHARES)),
        margin=Decimal(str(row.get("margin") or DEFAULT_MARGIN)),
        max_per_day=int(str(row.get("max_per_day") or DEFAULT_MAX_PER_DAY)),
        max_exposure_pct=Decimal(str(row.get("max_exposure_pct") or DEFAULT_MAX_EXPOSURE_PCT)),
    )
