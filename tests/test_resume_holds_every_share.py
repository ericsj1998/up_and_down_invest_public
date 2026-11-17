"""되살린 판은 열린 몫을 **전부** 세션 보유로 넣는다 (T852 S2).

예전 꼴: 판이 다시 뜰 때(배포 · 재시작) 첫 열린 기록만 세션 보유로 잡았다 — 한 포지션에 한 기록이던
시절의 전제다. 몫 판에서는 같은 종목에 열린 몫 여럿이 정상이라, 둘째 몫부터는 원장에만 열려 있고
세션이 몰랐다. 세션이 그 몫을 판정하지 않아 시간 청산 · 신호 청산 · 본전 옮기기가 안 돌았다
(거래소 손절 · 목표만 남음).

지키는 것:

- 몫으로 같이 들 수 있는 기록(다리마다 하나 · 같은 방향 · 몫 나눠 쓰기 선언)은 전부 보유로 넣는다.
- 같이 들 수 없는 겹침(같은 다리 둘 · 방향 섞임 · 몫 판이 아님)은 넣지 않고 돌려준다 — 사람에게
  알린다.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from types import SimpleNamespace
from typing import Any, cast

from test_share_runner import share
from updown.apps.api.walkforward import _hold_all  # pyright: ignore[reportPrivateUsage]
from updown.orchestration.walkforward.ledger import Direction, TradeRecord


class FakeSession:
    """`adopt` · `rehold` 로 받은 것만 적는 세션 대역."""

    def __init__(self, *, sharing: tuple[str, ...]) -> None:
        self.playbooks = [
            SimpleNamespace(attribution=name, share_same_side=name in sharing)
            for name in ("breakout@0.1.0", "channel@0.1.0", "crash@0.1.0")
        ]
        self.held: list[str] = []

    def adopt(self, record: TradeRecord) -> None:
        assert not self.held, "첫 기록만 adopt 로 넣는다"
        self.held.append(record.trade_id)

    def rehold(self, record: TradeRecord) -> None:
        self.held.append(record.trade_id)


ALL = ("breakout@0.1.0", "channel@0.1.0", "crash@0.1.0")
A = share("aaaaaaaa1111", stop="95", contracts=2, playbook="breakout@0.1.0")
B = share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0")
C = share("cccccccc3333", stop="88", contracts=1, playbook="crash@0.1.0")


def hold(session: FakeSession, held: list[TradeRecord]) -> list[TradeRecord]:
    return _hold_all(cast("Any", session), held)


def test_every_share_is_handed_to_the_session() -> None:
    session = FakeSession(sharing=ALL)
    assert hold(session, [A, B, C]) == []
    assert session.held == [A.trade_id, B.trade_id, C.trade_id], (
        "둘째 몫부터 빠지면 세션이 그 몫의 시간 청산 · 신호 청산 · 본전 옮기기를 안 돈다"
    )


def test_a_single_record_is_held_as_before() -> None:
    session = FakeSession(sharing=())
    assert hold(session, [A]) == []
    assert session.held == [A.trade_id]


def test_nothing_open_touches_nothing() -> None:
    session = FakeSession(sharing=ALL)
    assert hold(session, []) == [] and session.held == []


def test_two_records_of_the_same_leg_are_not_stacked() -> None:
    session = FakeSession(sharing=ALL)
    twin = dc_replace(A, trade_id="dddddddd4444")
    assert hold(session, [A, twin]) == [twin], "같은 다리 둘은 겹침이다 — 사람이 본다"
    assert session.held == [A.trade_id]


def test_opposite_directions_are_not_held_together() -> None:
    session = FakeSession(sharing=ALL)
    short = dc_replace(B, direction=Direction.SHORT)
    assert hold(session, [A, short]) == [short]
    assert session.held == [A.trade_id]


def test_a_board_without_share_legs_keeps_the_old_rule() -> None:
    session = FakeSession(sharing=())
    assert hold(session, [A, B]) == [B], "몫 판이 아니면 열린 기록 둘은 사고의 잔해다"
    assert session.held == [A.trade_id]
