# ruff: noqa: ARG005, E501
# pyright: reportUnknownLambdaType=false, reportPrivateUsage=false, reportUnknownArgumentType=false
"""분기 우주 교체 실행 (T758 R1 · R3 · R4 · R7 꼴) — 가짜 세션 · 가짜 조정자로 `_universe_step` 을 돌린다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

import updown.apps.api.rebalancer as rb
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer.legs import FundLeg
from updown.orchestration.rebalancer.rotation import RotationConfig
from updown.orchestration.walkforward.ledger import Outcome

NOW = datetime(2026, 10, 1, 0, 20, tzinfo=UTC)
CFG = RotationConfig(stagger_s=0.0, batch=10)


@dataclass
class _Rec:
    outcome: Outcome


@dataclass
class _Ledger:
    records: list[_Rec] = field(default_factory=list[_Rec])


@dataclass
class _Session:
    ledger: _Ledger = field(default_factory=_Ledger)
    entry_legs: frozenset[str] | None = None

    def pending_entry(self) -> None:
        return None


@dataclass
class _Live:
    session: _Session


@dataclass
class _Engine:
    basket: Basket
    balance: Decimal = Decimal(1000)


@dataclass
class _Coord:
    engine: _Engine
    ports: dict[str, Any] = field(default_factory=dict[str, Any])
    released: list[str] = field(default_factory=list[str])

    def release(self, symbol: str) -> None:
        self.released.append(symbol)
        self.ports.pop(symbol, None)


@dataclass
class _Report:
    budgets: dict[str, Decimal]


def _leg(name: str, *symbols: str) -> FundLeg:
    return FundLeg(
        playbook=name,
        attribution=f"{name}@0",
        symbols=symbols,
        leverage=Decimal(4),
        exposure=Decimal(4),
        timeframe="4h",
        slots=6,
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    sessions: dict[str, _Live] = {
        "hA": _Live(_Session()),
        "hX": _Live(_Session()),
        "hY": _Live(_Session(_Ledger([_Rec(Outcome.OPEN)]))),
    }
    spawned: list[str] = []
    dropped: list[str] = []
    snap = {"tri": ("A_USDT", "B_USDT"), "macd": ("B_USDT",)}

    async def fake_legs(profile: str, quarter: date) -> dict[str, tuple[str, ...]] | None:
        del profile
        return snap if quarter == date(2026, 10, 1) else None

    async def fake_spawn(symbol: str, *args: Any, **kw: Any) -> tuple[str, Any]:
        del args, kw
        spawned.append(symbol)
        handle = f"h{symbol[0]}new"
        live = _Live(_Session())
        sessions[handle] = live
        return handle, live

    async def fake_drop(handle: str) -> None:
        dropped.append(handle)
        sessions.pop(handle, None)

    async def fake_tick(fund: Any) -> _Report:
        return _Report({s: Decimal(100) for s in fund.coordinator.engine.basket.symbols})

    monkeypatch.setattr(rb, "SESSIONS", sessions)
    monkeypatch.setattr(rb, "_universe_legs", fake_legs)
    monkeypatch.setattr(rb, "_spawn_session", fake_spawn)
    monkeypatch.setattr(rb, "_drop_one", fake_drop)
    monkeypatch.setattr(rb, "_tick", fake_tick)
    monkeypatch.setattr(rb, "_reattach_gate", lambda fund: None)
    monkeypatch.setattr(rb, "_save_fund", lambda fund: None)
    delisted: set[str] = set()
    closed: list[str] = []

    async def fake_delist(now: datetime, cfg: RotationConfig) -> frozenset[str]:
        del now, cfg
        return frozenset(delisted)

    async def fake_close(handle: str) -> dict[str, Any]:
        closed.append(handle)
        return {}

    monkeypatch.setattr(rb, "_delisting_now", fake_delist)
    monkeypatch.setattr(rb, "_close_live_position", fake_close)
    rb._DELIST_CLOSED.clear()
    monkeypatch.setattr(
        rb, "member_playbook", lambda legs, sym: "+".join(rb._legs_of_symbol(legs, sym))
    )  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(rb, "member_leverage", lambda legs, sym: Decimal(4))
    basket = Basket(as_members([(s, Decimal(1)) for s in ("A_USDT", "X_USDT", "Y_USDT")]))
    fund = rb.Fund(
        fund_id="f1",
        label="t",
        coordinator=_Coord(_Engine(basket)),  # type: ignore[arg-type]
        handles={"A_USDT": "hA", "X_USDT": "hX", "Y_USDT": "hY"},
        leverage=Decimal(4),
        playbook="bundle",
        slots=6,
        legs=(_leg("tri", "A_USDT", "X_USDT"), _leg("macd", "Y_USDT")),
        universe="c0",
        universe_quarter="2026-07-01",
    )
    return {
        "fund": fund,
        "sessions": sessions,
        "spawned": spawned,
        "dropped": dropped,
        "delisted": delisted,
        "closed": closed,
    }


def _step(world: dict[str, Any], now: datetime = NOW, cfg: RotationConfig = CFG) -> dict[str, Any]:
    return asyncio.run(rb._universe_step(world["fund"], now, cfg))  # pyright: ignore[reportPrivateUsage]


def test_rotation_drops_flat_winds_down_held_spawns_new(world: dict[str, Any]) -> None:
    out = _step(world)
    fund = world["fund"]
    assert world["dropped"] == ["hX"]  # 평평 · 목록 밖
    assert world["spawned"] == ["B_USDT"]
    assert "Y_USDT" in fund.handles  # 포지션 있음 → 청산 대기
    assert world["sessions"]["hY"].session.entry_legs == frozenset()  # 신규 진입 0
    assert fund.entry_legs["B_USDT"] == ("macd", "tri")
    legs = {leg.playbook: leg.symbols for leg in fund.legs}
    assert legs == {"tri": ("A_USDT", "B_USDT"), "macd": ("B_USDT", "Y_USDT")}
    assert set(fund.coordinator.engine.basket.symbols) == {"A_USDT", "B_USDT", "Y_USDT"}
    assert fund.universe_quarter == "2026-10-01"
    assert out["left"] == 0


def test_second_check_has_nothing_to_do(world: dict[str, Any]) -> None:
    _step(world)
    out = _step(world)
    assert out["done"] == [] and out["left"] == 0


def test_quiet_minutes_narrow_entries_but_do_not_spawn_or_drop(world: dict[str, Any]) -> None:
    out = _step(world, now=datetime(2026, 10, 1, 0, 50, tzinfo=UTC))
    assert out["skipped"] == "quiet"
    assert world["spawned"] == [] and world["dropped"] == []
    assert world["sessions"]["hX"].session.entry_legs == frozenset()  # 거둘 것도 진입은 먼저 0
    assert world["sessions"]["hY"].session.entry_legs == frozenset()


def test_memory_hold_skips_spawn_but_still_drops(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rb, "_rss_mb", lambda: 749.0)
    out = _step(world)
    assert out["skipped"] == "memory"
    assert world["spawned"] == []
    assert world["dropped"] == ["hX"]


def test_held_position_closed_later_converges_to_drop(world: dict[str, Any]) -> None:
    _step(world)
    world["sessions"]["hY"].session.ledger.records.clear()  # 청산됨
    _step(world)
    assert "Y_USDT" not in world["fund"].handles
    assert world["dropped"] == ["hX", "hY"]


def test_missing_snapshot_keeps_last_quarter_and_does_nothing_new(world: dict[str, Any]) -> None:
    world["fund"].universe_quarter = "2026-07-01"
    out = _step(world, now=datetime(2027, 1, 1, 0, 20, tzinfo=UTC))
    assert out["action"] == "no_snapshot"
    assert world["spawned"] == [] and world["dropped"] == []


def test_delisting_notice_zeroes_entries_and_closes_held_once(world: dict[str, Any]) -> None:
    world["delisted"].update({"A_USDT", "Y_USDT"})
    _step(world)
    assert world["sessions"]["hA"].session.entry_legs == frozenset()  # 목록 안이어도 진입 0
    assert world["closed"] == ["hY"]  # 보유만 닫는다
    _step(world)
    assert world["closed"] == ["hY"]  # 같은 공지로 거듭 닫지 않는다
