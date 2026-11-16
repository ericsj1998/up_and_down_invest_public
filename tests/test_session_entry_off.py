"""선언 `entry_off` — 다리 하나의 신규 진입만 끄고, 같은 종목 다른 다리 · 열린 포지션은 그대로."""

from __future__ import annotations

from dataclasses import replace

from test_session_entry_legs import LEG_A, LEG_B, flat_session, offer
from updown.orchestration.walkforward.ledger import Outcome

OFF_A = replace(LEG_A, entry_off=True)


def test_default_is_off_false() -> None:
    assert LEG_A.entry_off is False


def test_entry_off_leg_does_not_enter_and_is_counted() -> None:
    s = flat_session((OFF_A, LEG_B))
    assert offer(s, OFF_A) is None
    assert s.entry_off_blocked == 1
    assert not s.ledger.records


def test_other_leg_of_same_symbol_still_enters() -> None:
    s = flat_session((OFF_A, LEG_B))
    got = offer(s, OFF_A, LEG_B)
    assert got is not None
    assert got.playbook == LEG_B.attribution
    assert s.entry_off_blocked == 1


def test_open_position_keeps_being_managed() -> None:
    s = flat_session((LEG_A,))
    held = offer(s, LEG_A)
    assert held is not None
    s.playbooks = (OFF_A,)  # 보유 중에 진입을 껐다(배포로 선언이 바뀐 것과 같다)
    s.step()
    still = [r for r in s.ledger.records if r.trade_id == held.trade_id]
    assert still and still[0].outcome is Outcome.OPEN
