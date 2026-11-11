"""T820 — 처음 손절(`TradeRecord.initial_stop`)이 트레일 · 청산 · 옛 참조 사본을 넘어 남는다.

표시 전용이다.

    ① 세션이 원장에 넣기 직전 손절이 처음 손절이 된다 ② 이어받은 포지션은 비운다
    ③ 이미 있으면 덮지 않는다 ④ 원장은 넣은 기록을 바꾸지 않는다(세션 · 원장이 같은 기록)
    ⑤ 손절이 올라간 사본으로 갈아 끼워도 처음 값은 그대로 ⑥ 닫힌 사본에도 남는다
    ⑦ 세션을 걸으면 시스템 매매마다 채워지고, 세션이 든 기록이 원장 기록과 같다
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from test_sample_end_to_end import _session  # pyright: ignore[reportPrivateUsage]
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Ledger,
    Outcome,
    TradeRecord,
    pin_initial_stop,
)

AT = datetime(2026, 11, 11, 4, tzinfo=UTC)


def _rec(**over: object) -> TradeRecord:
    fields: dict[str, object] = {
        "trade_id": "t1",
        "playbook": "sample@0.1",
        "actor": Actor.SYSTEM,
        "direction": Direction.LONG,
        "placed_at": AT,
        "opened_at": AT,
        "entry": Decimal(100),
        "planned_stop": Decimal(95),
        "planned_first": Decimal(110),
        "planned_target": Decimal(120),
        "outcome": Outcome.OPEN,
        "cost_pct": Decimal("0.001"),
    }
    fields.update(over)
    return TradeRecord(**fields)  # pyright: ignore[reportArgumentType]


def test_pin_takes_the_stop_at_that_moment() -> None:
    assert pin_initial_stop(_rec()).initial_stop == Decimal(95)


def test_adopted_positions_stay_unknown() -> None:
    got = pin_initial_stop(_rec(actor=Actor.ADOPTED))
    assert got.initial_stop is None, "거래소 손절이 이미 옮겨졌을 수 있다"


def test_an_existing_value_is_not_overwritten() -> None:
    assert pin_initial_stop(_rec(initial_stop=Decimal(93))).initial_stop == Decimal(93)


def test_the_ledger_stores_what_it_is_given() -> None:
    book = Ledger()
    held = _rec()
    book.add(held)
    assert book.records[0] is held, "원장이 사본을 만들면 세션이 든 기록과 갈린다"


def test_a_trailed_copy_from_an_old_reference_keeps_the_first_stop() -> None:
    book = Ledger()
    book.add(pin_initial_stop(_rec()))
    book.replace(replace(_rec(), planned_stop=Decimal(99)))
    got = book.records[0]
    assert (got.planned_stop, got.initial_stop) == (Decimal(99), Decimal(95))


def test_closing_keeps_it() -> None:
    done = _rec(initial_stop=Decimal(95)).closed(
        at=AT, price=Decimal(104), outcome=Outcome.TAKE_PROFIT
    )
    assert done.initial_stop == Decimal(95)


def test_a_walked_session_fills_every_system_trade() -> None:
    session = _session()
    for _ in range(20_000):
        if session.finished:
            break
        session.step()
    system = [r for r in session.ledger.records if r.actor is Actor.SYSTEM]
    assert system, "전제: 예시 매매법이 한 번은 산다"
    assert all(r.initial_stop is not None and r.initial_stop > 0 for r in system)


def test_a_manual_buy_pins_it_and_the_session_holds_the_same_record() -> None:
    session = _session()
    session.step()
    made = session.buy(Decimal(100), Decimal(95), Decimal(120))
    assert made.initial_stop == Decimal(95)
    assert made in session.ledger.records
