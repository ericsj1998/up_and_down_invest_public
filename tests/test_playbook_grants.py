"""매매법별 권한 — 보기 · 백테스트 · 사용 (T230 · 사용자 2026-09-08).

막아야 하는 실패:

1. 게스트가 실제 매매법의 백테스트를 보거나, 열람자가 판을 여는 것 (묶음 기본값).
2. 사람별 덮어쓰기가 묶음보다 약해지는 것 — 행이 있으면 그 행이 정한다.
3. 감사가 백테스트만 열고 거래까지 여는 것.
4. 보기 없는 백테스트/사용 조합이 저장되는 것.
5. 표(JSON)에서 읽은 정책이 쓴 것과 달라지는 것.
"""

from __future__ import annotations

import pytest

from updown.apps.api import auth as mod
from updown.common.security.caps import Cap, policy_of
from updown.common.security.playbooks import (
    ALL,
    BUILTIN_POLICIES,
    SAMPLE_PLAYBOOK,
    PlaybookGrant,
    PlaybookPolicy,
    effective_grant,
)
from updown.common.security.roles import Role

REAL = "private_strategy"


class TestPolicy:
    def test_json_round_trip_keeps_star_and_sorted_lists(self) -> None:
        policy = PlaybookPolicy(view=ALL, backtest=frozenset({"b", "a"}), trade=frozenset())
        raw = policy.to_json()
        assert raw == {"view": "*", "backtest": ["a", "b"], "trade": []}
        assert PlaybookPolicy.from_json(raw) == policy

    def test_unknown_shape_opens_nothing(self) -> None:
        empty = PlaybookPolicy.from_json("garbage")
        assert not empty.allows("view", REAL) and not empty.allows("trade", SAMPLE_PLAYBOOK)

    def test_builtin_defaults_match_the_user_decision(self) -> None:
        guest = BUILTIN_POLICIES["guest"]
        assert guest.allows("view", REAL) and guest.allows("trade", REAL)
        assert guest.allows("backtest", SAMPLE_PLAYBOOK) and not guest.allows("backtest", REAL)
        viewer = BUILTIN_POLICIES["viewer"]
        assert viewer.allows("view", REAL) and not viewer.allows("trade", SAMPLE_PLAYBOOK)
        trader = BUILTIN_POLICIES["trader"]
        assert trader.allows("backtest", REAL) and trader.allows("trade", REAL)


class TestEffectiveGrant:
    def test_row_overrides_policy(self) -> None:
        row = PlaybookGrant(REAL, view=True, backtest=True, trade=False)
        got = effective_grant(REAL, policy=BUILTIN_POLICIES["trader"], row=row, audit=False)
        assert got.backtest and not got.trade

    def test_audit_opens_view_and_backtest_only(self) -> None:
        got = effective_grant(REAL, policy=BUILTIN_POLICIES["viewer"], row=None, audit=True)
        assert got.view and got.backtest and not got.trade

    def test_view_is_required_for_the_other_two(self) -> None:
        with pytest.raises(ValueError, match="보기"):
            PlaybookGrant(REAL, view=False, backtest=True, trade=False).validate()
        PlaybookGrant(REAL, view=False, backtest=False, trade=False).validate()


class TestCaller:
    def test_role_only_caller_uses_the_legacy_collection_policy(self) -> None:
        guest = mod.Caller(email="g@example.com", role=Role.GUEST, fresh=True)
        assert guest.playbook(REAL).view and not guest.playbook(REAL).backtest
        assert guest.playbook(SAMPLE_PLAYBOOK).backtest and guest.playbook(REAL).trade
        viewer = mod.Caller(email="v@example.com", role=Role.VIEWER, fresh=True)
        assert not viewer.playbook(REAL).trade

    def test_rows_and_audit_compose(self) -> None:
        who = mod.Caller(
            email="t@example.com",
            role=Role.TRADER,
            fresh=True,
            caps=frozenset({Cap.AUDIT}),
            playbook_rows={REAL: PlaybookGrant(REAL, view=True, backtest=False, trade=False)},
        )
        got = who.playbook(REAL)
        assert got.backtest  # 감사가 연다
        assert not got.trade  # 행이 막는다

    def test_policy_of_falls_back_to_builtin_then_default(self) -> None:
        assert policy_of(None, Role.GUEST) == BUILTIN_POLICIES["guest"]
        assert policy_of(None, Role.PENDING).allows("backtest", SAMPLE_PLAYBOOK)
        assert not policy_of(None, Role.PENDING).allows("trade", SAMPLE_PLAYBOOK)
