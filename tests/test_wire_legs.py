"""다리 문 배선 — 실계좌 펀드가 40판에 다리 문을 끼우는 결과를 고정한다 (T309 ①).

`apps/api/rebalancer._wire_gate` 의 다리 가지를 펀드 재현 도구(T309)가 부를 수 있게
`orchestration` 으로 꺼내기 **전에** 지금 결과를 먼저 못박았다.
옛 경로와 새 경로가 같은 결과를 내야 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from updown.analysis.playbook.select import load_playbooks
from updown.apps.api import rebalancer as rb
from updown.common.domain.instrument import Timeframe
from updown.orchestration.rebalancer.legs import FundLeg, declared_legs
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.rebalancer.wiring import wire_legs

ROOT = Path(__file__).resolve().parents[1]
WRAP = "private_strategy"


def mixed_legs() -> tuple[FundLeg, ...]:
    raw = yaml.safe_load((ROOT / "config" / "baskets.yml").read_text(encoding="utf-8"))
    scopes = {
        name: [str(row["symbol"]) for row in body["members"]]
        for name, body in raw["by_playbook"].items()
    }
    books = load_playbooks()
    wrapper = next(item for item in books if item.playbook_id == WRAP)
    return declared_legs(wrapper, books, scopes, scopes[WRAP])


def via_api(coordinator: Any, legs: tuple[FundLeg, ...]) -> None:
    rb._wire_gate(coordinator, 0, 0, legs=legs)  # pyright: ignore[reportPrivateUsage]


def via_orchestration(coordinator: Any, legs: tuple[FundLeg, ...]) -> None:
    ledger = coordinator.engine.ledger
    wire_legs(coordinator.ports, legs, lambda: ledger.drawdown_pct / Decimal(100))


PATHS: list[Callable[[Any, tuple[FundLeg, ...]], None]] = [via_api, via_orchestration]


def wired(path: Callable[[Any, tuple[FundLeg, ...]], None]) -> tuple[Any, ...]:
    legs = mixed_legs()
    symbols = sorted({s for leg in legs for s in leg.symbols})
    ports = {
        sym: SessionBridge(
            session=cast(
                "Any",
                SimpleNamespace(
                    instrument=SimpleNamespace(symbol=sym), entry_gate=None, leg_leverage=None
                ),
            )
        )
        for sym in symbols
    }
    ledger = SimpleNamespace(drawdown_pct=Decimal(0))
    coordinator = SimpleNamespace(engine=SimpleNamespace(ledger=ledger), ports=ports)
    path(coordinator, legs)
    return legs, ports, ledger


@pytest.mark.parametrize("path", PATHS)
class TestWireLegs:
    def test_every_board_shares_one_leg_gate(self, path: Any) -> None:
        _legs, ports, _ledger = wired(path)
        gates = {id(p.session.entry_gate) for p in ports.values()}
        assert len(ports) == 40 and len(gates) == 1

    def test_each_board_carries_the_exposure_of_its_legs(self, path: Any) -> None:
        legs, ports, _ledger = wired(path)
        long_leg, tri_leg, macd_leg = legs
        btc, gala, zec = ports["BTC_USDT"], ports["GALA_USDT"], ports["ZEC_USDT"]
        assert btc.session.leg_leverage == {
            long_leg.attribution: Decimal(4),
            tri_leg.attribution: Decimal(2),
        }
        assert gala.session.leg_leverage == {tri_leg.attribution: Decimal(2)}
        assert zec.session.leg_leverage == {macd_leg.attribution: Decimal("1.5")}

    def test_breadth_is_counted_on_the_core_boards_only(self, path: Any) -> None:
        legs, ports, _ledger = wired(path)
        core = set(legs[0].symbols)
        for sym, port in ports.items():
            want = (3, Timeframe.H1) if sym in core else (0, None)
            assert (port.breadth_bars, port.breadth_frame) == want, sym

    def test_drawdown_is_read_from_the_ledger_every_time(self, path: Any) -> None:
        legs, ports, ledger = wired(path)
        gate = ports["BTC_USDT"].session.entry_gate
        long_gate = gate.legs[legs[0].attribution]
        macd_gate = gate.legs[legs[2].attribution]
        ledger.drawdown_pct = Decimal("12")
        assert long_gate.drawdown() == Decimal("0.12") == macd_gate.drawdown()
        ledger.drawdown_pct = Decimal("3")
        assert long_gate.drawdown() == Decimal("0.03")
        assert (long_gate.brake_at, long_gate.brake_scale) == (Decimal("0.10"), Decimal("0.5"))
        assert macd_gate.halt_dd_at == 0  # 411차 — 끄지 않고 x0.25
        assert (macd_gate.brake_at, macd_gate.brake_scale) == (Decimal("0.10"), Decimal("0.25"))
