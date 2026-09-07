"""계정의 **처지(standing)** — 등급과 별개로 "지금 들어올 수 있나" (사용자 2026-09-07).

> *"게스트가 구글 계정으로 진입했을 때, 하루가 지나기 전까지 관리자로부터 허가를 받지 못하면
> 임시 보류 상태로 차단됐으면 좋겠어."*

등급(`Role`)은 **무엇을 할 수 있나**이고, 처지는 **문 앞에 설 수 있나**다. 둘을 섞으면
"승인 대기인데 하루가 지났다" 를 등급으로 표현해야 하고, 그러면 등급 표에 시간이 들어간다.
여기서 시간을 따로 계산해 네 가지로 낸다:

    ACTIVE   승인됐다(열람자·거래자·관리자) 또는 게스트 — 등급이 정하는 대로
    PENDING  승인 대기 · 가입한 지 하루 안 — 읽기는 된다 (데모를 둘러본다)
    HELD     승인 대기 · 하루가 지났다 · 관리자가 보류를 풀어 주지 않았다 — **아무것도 못 한다**
    BLOCKED  관리자가 차단했다 — 로그인조차 거절

🔴 **순수 함수다.** 현재 시각을 인자로 받는다 — 시험이 시계를 쥔다 (절대 규칙 #5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from updown.common.security.roles import Role

HOLD_AFTER = timedelta(hours=24)
"""가입 뒤 이 시간 안에 승인되지 않으면 보류된다 (사용자 확정 2026-09-07 "하루")."""

HOLD_MESSAGE = "임시 보류 상태입니다. 관리자에게 문의하세요."
"""보류된 사람이 보는 한 줄 — 화면 카드와 API 403 본문이 **같은 글자**를 쓴다."""

CONTACT_COOLDOWN = timedelta(minutes=30)
"""관리자 문의 메일을 다시 보낼 수 있기까지의 간격 — 단추 연타가 관리자 받은편지함을 채우지 않게."""


class Standing(StrEnum):
    """계정의 처지 — 위 모듈 설명 참고."""

    ACTIVE = "active"
    PENDING = "pending"
    HELD = "held"
    BLOCKED = "blocked"


def hold_starts_at(
    created_at: datetime | None, *, hold_after: timedelta = HOLD_AFTER
) -> datetime | None:
    """보류가 시작되는 시각 — 가입 시각 + 유예.

    Args:
        created_at: 가입 시각. None 이면(옛 행 · 아직 안 만들어짐) None.
        hold_after: 유예. 기본은 `HOLD_AFTER`(24h) — 관리자가 설정으로 바꾼다 (2026-09-07).

    Returns:
        보류 시작 시각 (UTC aware).
    """
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at + hold_after


def standing_of(
    *,
    role: Role,
    blocked: bool,
    created_at: datetime | None,
    hold_released_until: datetime | None,
    now: datetime,
    hold_after: timedelta = HOLD_AFTER,
) -> Standing:
    """이 계정이 지금 어떤 처지인가.

    Args:
        role: 등급.
        blocked: 관리자 차단 플래그.
        created_at: 가입 시각.
        hold_released_until: 관리자가 보류를 풀어 준 기한. 이 시각 전에는 보류되지 않는다.
        now: 현재 시각 (UTC aware).
        hold_after: 가입 뒤 유예. 관리자 설정값이 들어온다 (기본 24h).

    Returns:
        처지. 차단이 등급보다 먼저고, 보류는 **승인 대기(`PENDING`)에만** 있다 — 승인된 사람은
        하루가 지나도 보류되지 않는다 (그 사람은 관리자가 이미 판단했다).

    Note:
        ⚠️ `created_at` 이 없으면(옛 행) 보류하지 않는다 — 언제 왔는지 모르는 사람을
        시계 때문에 막는 것은 잘못된 쪽으로 틀리는 것이다. 차단은 관리자가 직접 한다.
    """
    if blocked:
        return Standing.BLOCKED
    if role is not Role.PENDING:
        return Standing.ACTIVE
    starts = hold_starts_at(created_at, hold_after=hold_after)
    if starts is None or now < starts:
        return Standing.PENDING
    if hold_released_until is not None:
        until = hold_released_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if now < until:
            return Standing.PENDING
    return Standing.HELD


def contact_allowed(last_contact_at: datetime | None, now: datetime) -> bool:
    """관리자 문의를 지금 보내도 되나 — 마지막 문의로부터 `CONTACT_COOLDOWN` 이 지났나."""
    if last_contact_at is None:
        return True
    if last_contact_at.tzinfo is None:
        last_contact_at = last_contact_at.replace(tzinfo=UTC)
    return now - last_contact_at >= CONTACT_COOLDOWN
