"""분기 우주 교체 계획 (T758 R1 · R2 꼴) — 포지션이 있는 종목은 다시 띄우지 않는다."""

from __future__ import annotations

from updown.orchestration.rebalancer.rotation import (
    DROP,
    KEEP,
    NARROW,
    RESPAWN,
    SPAWN,
    WIND_DOWN,
    MemberAction,
    leg_symbols,
    plan_rotation,
)

SNAP = {"tri": ("A_USDT", "B_USDT"), "macd": ("B_USDT", "C_USDT")}


def _by(actions: tuple[MemberAction, ...]) -> dict[str, MemberAction]:
    return {a.symbol: a for a in actions}


def test_new_symbol_spawns_with_its_legs() -> None:
    got = _by(plan_rotation(SNAP, {}, {}))
    assert got["B_USDT"].action == SPAWN
    assert got["B_USDT"].legs == ("macd", "tri") == got["B_USDT"].entry_legs


def test_same_set_keeps() -> None:
    got = _by(plan_rotation(SNAP, {"A_USDT": ("tri",)}, {}))
    assert got["A_USDT"].action == KEEP


def test_flat_symbol_leaving_list_is_dropped_held_one_winds_down() -> None:
    running = {"X_USDT": ("tri",), "Y_USDT": ("macd",)}
    got = _by(plan_rotation(SNAP, running, {"Y_USDT": True}))
    assert got["X_USDT"].action == DROP and got["X_USDT"].legs == ()
    y = got["Y_USDT"]
    assert y.action == WIND_DOWN and y.legs == ("macd",) and y.entry_legs == ()


def test_set_change_respawns_only_when_flat() -> None:
    running = {"A_USDT": ("macd",)}  # 지난 분기 macd · 이번 분기 tri
    flat = _by(plan_rotation(SNAP, running, {}))["A_USDT"]
    assert flat.action == RESPAWN and flat.legs == ("tri",)
    held = _by(plan_rotation(SNAP, running, {"A_USDT": True}))["A_USDT"]
    assert held.action == NARROW
    assert held.legs == ("macd",)  # 세션은 그대로(이어받기 없음)
    assert held.entry_legs == ()  # 빠진 macd 는 청산만 · 새 tri 는 평평해진 뒤


def test_narrow_keeps_staying_leg_open_for_entries() -> None:
    running = {"B_USDT": ("macd", "old")}
    got = _by(plan_rotation(SNAP, running, {"B_USDT": True}))["B_USDT"]
    assert got.action == NARROW and got.entry_legs == ("macd",)


def test_plan_is_deterministic_and_rerun_converges() -> None:
    running = {"A_USDT": ("macd",), "X_USDT": ("tri",)}
    first = plan_rotation(SNAP, running, {"A_USDT": True})
    assert first == plan_rotation(SNAP, dict(reversed(list(running.items()))), {"A_USDT": True})
    # 다 한 뒤 다시 계획하면(포지션이 닫혀 평평해진 A 는 RESPAWN · 나머지 KEEP)
    after = {a.symbol: a.legs for a in first if a.legs}
    second = _by(plan_rotation(SNAP, after, {}))
    assert second["A_USDT"].action == RESPAWN
    assert {s for s, a in second.items() if a.action == KEEP} == {"B_USDT", "C_USDT"}


def test_leg_symbols_is_what_sessions_carry() -> None:
    running = {"Y_USDT": ("macd",)}
    acts = plan_rotation(SNAP, running, {"Y_USDT": True})
    got = leg_symbols(acts)
    assert got["macd"] == ("B_USDT", "C_USDT", "Y_USDT")  # 청산 대기 종목도 다리 범위 안
    assert got["tri"] == ("A_USDT", "B_USDT")
