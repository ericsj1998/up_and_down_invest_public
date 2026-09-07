"""기능별 권한(Cap)·권한 묶음 진리표 (사용자 2026-09-07).

막아야 하는 실패:
1. 데모 서버의 조회 경로가 실거래 조회 기능을 요구한다(또는 반대) — 같은 경로, 다른 서버.
2. 관리자가 관리자를 찍어 낸다 — 관리 기능이 든 묶음은 슈퍼 관리자만 준다.
3. 묶음이 지워졌는데 계정이 전부 잃는다 — 등급 기본으로 떨어져야 한다.
4. 새 POST 경로가 아무 기능도 요구하지 않는다.
5. 옛 등급이 묶음과 어긋난다 — 묶음에서 되계산한 등급이 화면·보류 판정의 기준이다.
"""

from __future__ import annotations

import pytest

from updown.common.security.caps import (
    BUILTIN_BY_NAME,
    PENDING_CAPS,
    Access,
    Cap,
    caps_for_role,
    dump_caps,
    effective_caps,
    may_assign,
    parse_caps,
    required_cap,
    role_for,
)
from updown.common.security.roles import Role


class TestRequiredCap:
    @pytest.mark.parametrize(
        ("method", "path", "live", "want"),
        [
            ("GET", "/health", False, Access.PUBLIC),
            ("GET", "/auth/me", True, Access.PUBLIC),
            ("GET", "/analysis/x", False, Access.SIGNED_IN),
            ("GET", "/exchange/state", False, Cap.DEMO_ACCOUNT_READ),
            ("GET", "/exchange/state", True, Cap.LIVE_ACCOUNT_READ),
            ("GET", "/walkforward/sessions", False, Cap.DEMO_RUNS_READ),
            # 실계좌에서도 RUN 목록은 로그인만 하면 본다 (옛 MONEY_READ_PREFIXES 와 같은 판정).
            ("GET", "/walkforward/sessions", True, Access.SIGNED_IN),
            ("GET", "/walkforward/live/abc", True, Cap.LIVE_RUNS_READ),
            ("GET", "/rebalancer/x", True, Cap.LIVE_RUNS_READ),
            ("POST", "/walkforward/live", False, Cap.DEMO_TRADE),
            ("POST", "/walkforward/live", True, Cap.LIVE_TRADE),
            # 수정·삭제는 거래와 다른 기능이다 (사용자 2026-09-08)
            ("DELETE", "/rebalancer/f1", False, Cap.DEMO_DELETE),
            ("DELETE", "/walkforward/sessions/k", True, Cap.LIVE_DELETE),
            ("DELETE", "/exchange/orders/1", True, Cap.LIVE_DELETE),
            ("PUT", "/rebalancer/f1/basket", False, Cap.DEMO_EDIT),
            ("PUT", "/rebalancer/f1/playbook", True, Cap.LIVE_EDIT),
            ("POST", "/walkforward/live/k/auto", True, Cap.LIVE_EDIT),
            ("POST", "/walkforward/live/k/leverage", False, Cap.DEMO_EDIT),
            ("POST", "/rebalancer/f1/resync", True, Cap.LIVE_EDIT),
            ("POST", "/rebalancer/f1/deposit", True, Cap.LIVE_TRADE),
            ("POST", "/rebalancer/f1/tick", False, Cap.DEMO_TRADE),
            ("POST", "/exchange/close", True, Cap.LIVE_TRADE),
            ("POST", "/아직/없는/기능", True, Cap.LIVE_TRADE),
            ("GET", "/report/daily", True, Cap.REPORT),
            ("POST", "/report/send", False, Cap.REPORT),
            ("GET", "/auth/users", False, Cap.MANAGE_USERS),
            ("POST", "/auth/users/a@b.c/collection", True, Cap.MANAGE_USERS),
            ("GET", "/auth/roles", False, Cap.MANAGE_USERS),
            ("PUT", "/auth/roles/guest", False, Cap.MANAGE_ROLES),
            ("DELETE", "/auth/roles/x", True, Cap.MANAGE_ROLES),
            ("GET", "/admin/logs", False, Cap.MANAGE_USERS),
        ],
    )
    def test_table(self, method: str, path: str, live: bool, want: Cap | Access) -> None:
        assert required_cap(method, path, live=live) is want


class TestBuiltins:
    def test_guest_is_what_the_user_described(self) -> None:
        assert BUILTIN_BY_NAME["guest"].caps == {
            Cap.DEMO_TRADE,
            Cap.DEMO_EDIT,
            Cap.DEMO_ACCOUNT_READ,
            Cap.DEMO_RUNS_READ,
            Cap.REPORT,
        }
        # 게스트는 지우지 못하고, 거래자는 수정·삭제까지 다 한다 (2026-09-08)
        assert Cap.DEMO_DELETE not in BUILTIN_BY_NAME["guest"].caps
        assert {Cap.LIVE_EDIT, Cap.LIVE_DELETE, Cap.DEMO_EDIT, Cap.DEMO_DELETE} <= BUILTIN_BY_NAME[
            "trader"
        ].caps

    def test_only_super_admin_edits_collections(self) -> None:
        for name, item in BUILTIN_BY_NAME.items():
            assert (Cap.MANAGE_ROLES in item.caps) is (name == "super_admin")

    def test_legacy_roles_map_to_builtin_caps(self) -> None:
        assert caps_for_role(Role.PENDING) == PENDING_CAPS
        assert caps_for_role(Role.VIEWER) == BUILTIN_BY_NAME["viewer"].caps
        assert caps_for_role(Role.ADMIN) == BUILTIN_BY_NAME["super_admin"].caps
        assert caps_for_role(None) == frozenset()

    def test_pending_reads_demo_but_never_trades(self) -> None:
        assert Cap.DEMO_ACCOUNT_READ in PENDING_CAPS
        assert not PENDING_CAPS & {Cap.DEMO_TRADE, Cap.LIVE_TRADE, Cap.LIVE_ACCOUNT_READ}


class TestEffectiveCaps:
    def test_collection_plus_extra(self) -> None:
        got = effective_caps(
            role=Role.VIEWER,
            collection="viewer",
            extra=frozenset({Cap.DEMO_TRADE}),
            table=dict(BUILTIN_BY_NAME),
        )
        assert got == BUILTIN_BY_NAME["viewer"].caps | {Cap.DEMO_TRADE}

    def test_deleted_collection_falls_back_to_role_not_nothing(self) -> None:
        got = effective_caps(
            role=Role.TRADER, collection="gone", extra=frozenset(), table=dict(BUILTIN_BY_NAME)
        )
        assert got == BUILTIN_BY_NAME["trader"].caps

    def test_no_collection_is_pending(self) -> None:
        got = effective_caps(
            role=Role.PENDING, collection="", extra=frozenset(), table=dict(BUILTIN_BY_NAME)
        )
        assert got == PENDING_CAPS

    def test_admin_edits_to_a_collection_apply_to_everyone_in_it(self) -> None:
        table = dict(BUILTIN_BY_NAME)
        table["guest"] = BUILTIN_BY_NAME["guest"].__class__(
            "guest", "게스트", frozenset({Cap.REPORT}), builtin=True
        )
        got = effective_caps(role=Role.GUEST, collection="guest", extra=frozenset(), table=table)
        assert got == {Cap.REPORT}


class TestRoleFor:
    def test_derived_role_follows_the_caps(self) -> None:
        assert role_for(BUILTIN_BY_NAME["super_admin"].caps, has_collection=True) is Role.ADMIN
        assert role_for(BUILTIN_BY_NAME["admin"].caps, has_collection=True) is Role.ADMIN
        assert role_for(BUILTIN_BY_NAME["trader"].caps, has_collection=True) is Role.TRADER
        assert role_for(BUILTIN_BY_NAME["viewer"].caps, has_collection=True) is Role.VIEWER
        assert role_for(frozenset({Cap.DEMO_TRADE}), has_collection=False) is Role.PENDING
        assert role_for(frozenset(), has_collection=True, guest=True) is Role.GUEST


class TestMayAssign:
    def test_admin_grants_non_admin_caps_only(self) -> None:
        admin = BUILTIN_BY_NAME["admin"].caps
        assert may_assign(admin, BUILTIN_BY_NAME["trader"].caps)
        assert not may_assign(admin, BUILTIN_BY_NAME["admin"].caps)
        assert not may_assign(admin, {Cap.MANAGE_USERS})

    def test_super_admin_grants_anything_and_trader_nothing(self) -> None:
        assert may_assign(BUILTIN_BY_NAME["super_admin"].caps, BUILTIN_BY_NAME["super_admin"].caps)
        assert not may_assign(BUILTIN_BY_NAME["trader"].caps, {Cap.REPORT})


class TestSerialisation:
    def test_round_trip_is_ordered_and_ignores_junk(self) -> None:
        caps = parse_caps("report, demo_trade,nonsense,,audit")
        assert caps == {Cap.REPORT, Cap.DEMO_TRADE, Cap.AUDIT}
        assert dump_caps(caps) == "demo_trade,audit,report"
        assert parse_caps(dump_caps(caps)) == caps
        assert parse_caps(None) == frozenset()
