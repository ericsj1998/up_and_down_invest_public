"""계정 처지 — 승인 대기가 하루를 넘기면 보류된다 (사용자 2026-09-07).

막아야 하는 실패:
1. 하루 안의 대기가 보류된다 — 둘러볼 시간을 뺏는다.
2. 하루가 지나도 대기가 그대로 읽기를 한다 — 사용자가 막으려던 바로 그 상태.
3. 승인된 사람이 시계 때문에 보류된다 — 관리자가 이미 판단한 사람이다.
4. 관리자가 풀어 준 기한이 무시된다 / 기한이 지났는데 계속 열려 있다.
5. 차단이 처지 판정에서 등급보다 뒤에 온다.
6. 문의 연타 — 간격 안에는 거절.
7. 경로 표 — 문의 창구는 공개, 문의 목록은 관리자.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from updown.common.security.roles import Need, Role, need_for
from updown.common.security.standing import (
    CONTACT_COOLDOWN,
    HOLD_AFTER,
    Standing,
    contact_allowed,
    hold_starts_at,
    standing_of,
)

T0 = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def _standing(
    *,
    role: Role = Role.PENDING,
    blocked: bool = False,
    age: timedelta = timedelta(0),
    released: datetime | None = None,
) -> Standing:
    return standing_of(
        role=role,
        blocked=blocked,
        created_at=T0 - age,
        hold_released_until=released,
        now=T0,
    )


class TestStanding:
    def test_fresh_pending_is_pending(self) -> None:
        assert _standing(age=timedelta(hours=23, minutes=59)) is Standing.PENDING

    def test_pending_past_a_day_is_held(self) -> None:
        assert _standing(age=HOLD_AFTER) is Standing.HELD
        assert _standing(age=timedelta(days=30)) is Standing.HELD

    def test_approved_roles_never_hold(self) -> None:
        for role in (Role.VIEWER, Role.TRADER, Role.ADMIN, Role.GUEST):
            assert _standing(role=role, age=timedelta(days=400)) is Standing.ACTIVE

    def test_release_window_keeps_pending_then_holds_again(self) -> None:
        assert (
            _standing(age=timedelta(days=3), released=T0 + timedelta(hours=1)) is Standing.PENDING
        )
        assert _standing(age=timedelta(days=3), released=T0) is Standing.HELD
        assert _standing(age=timedelta(days=3), released=T0 - timedelta(days=1)) is Standing.HELD

    def test_blocked_wins_over_everything(self) -> None:
        assert _standing(role=Role.ADMIN, blocked=True) is Standing.BLOCKED
        assert _standing(blocked=True, released=T0 + timedelta(days=9)) is Standing.BLOCKED

    def test_unknown_creation_time_is_not_held(self) -> None:
        got = standing_of(
            role=Role.PENDING, blocked=False, created_at=None, hold_released_until=None, now=T0
        )
        assert got is Standing.PENDING

    def test_admin_setting_moves_the_line(self) -> None:
        """관리자가 유예를 48h 로 올리면 30h 된 대기는 아직 대기, 6h 로 내리면 7h 된 대기는 보류."""
        thirty = standing_of(
            role=Role.PENDING,
            blocked=False,
            created_at=T0 - timedelta(hours=30),
            hold_released_until=None,
            now=T0,
            hold_after=timedelta(hours=48),
        )
        assert thirty is Standing.PENDING
        seven = standing_of(
            role=Role.PENDING,
            blocked=False,
            created_at=T0 - timedelta(hours=7),
            hold_released_until=None,
            now=T0,
            hold_after=timedelta(hours=6),
        )
        assert seven is Standing.HELD
        assert hold_starts_at(T0, hold_after=timedelta(hours=6)) == T0 + timedelta(hours=6)

    def test_naive_timestamps_are_read_as_utc(self) -> None:
        naive = (T0 - timedelta(days=2)).replace(tzinfo=None)
        got = standing_of(
            role=Role.PENDING, blocked=False, created_at=naive, hold_released_until=None, now=T0
        )
        assert got is Standing.HELD
        assert hold_starts_at(naive) == T0 - timedelta(days=2) + HOLD_AFTER


class TestContactCooldown:
    def test_first_contact_and_after_cooldown_allowed(self) -> None:
        assert contact_allowed(None, T0)
        assert contact_allowed(T0 - CONTACT_COOLDOWN, T0)

    def test_inside_cooldown_refused(self) -> None:
        assert not contact_allowed(T0 - CONTACT_COOLDOWN + timedelta(seconds=1), T0)


class TestPaths:
    def test_contact_is_public_and_contacts_list_is_admin(self) -> None:
        assert need_for("POST", "/auth/contact") is Need.PUBLIC
        assert need_for("GET", "/auth/contacts") is Need.ADMIN
        assert need_for("POST", "/auth/users/a@b.c/hold") is Need.ADMIN
        assert need_for("DELETE", "/auth/users/a@b.c") is Need.ADMIN
        assert need_for("GET", "/auth/settings") is Need.ADMIN
        assert need_for("POST", "/auth/settings") is Need.ADMIN
        assert need_for("POST", "/auth/users/a@b.c/collection") is Need.ADMIN
        assert need_for("PUT", "/auth/roles/guest") is Need.ADMIN
