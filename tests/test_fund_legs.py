"""T291 — 이종 합성 매매법의 **다리**: 선언 → 다리 · 다리별 문 · 다리별 배율 · 되읽기 귀속.

지키는 것:

- `추세추종+삼각수렴`(private_strategy) 선언이 두 다리로 풀린다 — 돌파 롱은 핵심 6종, 삼각 숏은 18종 · 2x.
- 다리의 문은 **자기 매매만** 센다: 숏 다리의 자리가 꽉 차도 돌파 다리는 들어간다
  (측정이 다리마다 자리 6 이었다).
- 폭(조건부 상한)은 그 다리의 **종목에서만** 센다 — 18종으로 세면 "폭 ≥ 4" 가 흔해져
  다른 매매법이 된다.
- 모르는 다리는 막는다(규칙 #8-1) · 다리 선언이 서로 안 맞으면 펀드를 만들기 전에 터진다(#8).
- 다리가 없는 펀드·세션(지금까지의 전부)은 한 톨도 안 바뀐다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks
from updown.orchestration.rebalancer.gate import LegGate, LegPorts, SlotGate
from updown.orchestration.rebalancer.legs import (
    FundLeg,
    LegError,
    declared_legs,
    leg_gate,
    member_leverage,
    member_playbook,
)

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 9, 20, 12, tzinfo=UTC)
CORE = ("BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT")
LONG = "private_strategy"
SHORT = "private_strategy"


def scopes() -> dict[str, list[str]]:
    raw = yaml.safe_load((ROOT / "config" / "baskets.yml").read_text(encoding="utf-8"))
    return {
        name: [str(row["symbol"]) for row in body["members"]]
        for name, body in raw["by_playbook"].items()
    }


def real_legs() -> tuple[FundLeg, ...]:
    books = load_playbooks()
    wrapper = next(item for item in books if item.playbook_id == "private_strategy")
    return declared_legs(wrapper, books, scopes(), scopes()["private_strategy"])


@dataclass
class FakeSource:
    """`LegSource` — 다리별로 열린 수·노출을 손으로 심는다."""

    held: dict[str, int] = field(default_factory=dict[str, int])
    size: dict[str, Decimal] = field(default_factory=dict[str, Decimal])
    stops: dict[str, list[tuple[datetime, bool]]] = field(
        default_factory=dict[str, list[tuple[datetime, bool]]]
    )
    breaks: int = 0

    def open_count_of(self, leg: str) -> int:
        return self.held.get(leg, 0)

    def exits_of(self, leg: str) -> list[tuple[datetime, bool]]:
        return self.stops.get(leg, [])

    def open_exposure_of(self, leg: str) -> Decimal:
        return self.size.get(leg, Decimal(0))

    def band_breaks(self, at: datetime) -> int:  # noqa: ARG002
        return self.breaks


class TestTheDeclarationUnfoldsIntoTwoLegs:
    def test_long_leg_is_the_core_six_and_short_leg_is_eighteen(self) -> None:
        long_leg, short_leg = real_legs()
        assert (long_leg.playbook, short_leg.playbook) == (LONG, SHORT)
        assert set(long_leg.symbols) == set(CORE)
        assert len(short_leg.symbols) == 18 and set(CORE) <= set(short_leg.symbols)

    def test_each_leg_keeps_its_own_account_layer(self) -> None:
        long_leg, short_leg = real_legs()
        assert (long_leg.leverage, long_leg.slots, long_leg.halt_after_stops) == (Decimal(4), 6, 2)
        assert long_leg.notional_cap == Decimal(2) and long_leg.notional_fit
        assert long_leg.drawdown_brake is not None and long_leg.breadth_cap is not None
        assert long_leg.exposure == Decimal(4), "노출 선언이 없으면 거래소 배율과 같다"
        # 숏 다리: 노출 2x 를 격리 3x 로 든다 — 격리 2x 면 자리 6개가 계좌 증거금을 다 쓴다.
        assert (short_leg.leverage, short_leg.exposure, short_leg.slots) == (
            Decimal(3),
            Decimal(2),
            6,
        )
        assert short_leg.notional_cap is None and short_leg.drawdown_brake is None
        assert short_leg.breadth_cap is None and short_leg.halt_after_stops == 0

    def test_a_core_symbol_carries_both_legs_and_an_alt_only_the_short(self) -> None:
        legs = real_legs()
        assert member_playbook(legs, "BTC_USDT") == f"{LONG}+{SHORT}"
        assert member_playbook(legs, "GALA_USDT") == SHORT
        assert member_leverage(legs, "BTC_USDT") == Decimal(4)
        assert member_leverage(legs, "GALA_USDT") == Decimal(3)

    def test_the_short_leg_is_the_measured_playbook_plus_an_account_layer(self) -> None:
        books = {item.playbook_id: item for item in load_playbooks()}
        base, leg = books["private_strategy"], books[SHORT]
        for name in (
            "setups",
            "timeframe",
            "ma_exit_above_short",
            "entry_ref_return_band",
            "stop_mode",
            "hold_through_turn",
            "full_ride",
        ):
            assert getattr(base, name) == getattr(leg, name), name
        assert leg.listed is False

    def test_only_this_bundle_splits_its_legs(self) -> None:
        on = {item.playbook_id for item in load_playbooks() if item.split_legs}
        assert on == {"private_strategy", "private_strategy"}, "기존 묶음(추세+캐리)이 다리로 나뉘면 안 된다"

    def test_legs_survive_a_save_and_restore(self) -> None:
        for leg in real_legs():
            assert FundLeg.from_dict(leg.to_dict()) == leg


class TestBadDeclarationsStopBeforeAnySessionStarts:
    def test_a_symbol_no_leg_owns_is_refused(self) -> None:
        books = load_playbooks()
        wrapper = next(item for item in books if item.playbook_id == "private_strategy")
        with pytest.raises(LegError, match="어느 다리에도"):
            declared_legs(wrapper, books, scopes(), [*CORE, "PEPE_USDT"])

    def test_a_leg_without_a_scope_is_refused(self) -> None:
        books = load_playbooks()
        wrapper = next(item for item in books if item.playbook_id == "private_strategy")
        with pytest.raises(LegError, match="종목 범위"):
            declared_legs(wrapper, books, {LONG: list(CORE)}, list(CORE))

    def test_a_leg_with_other_slots_is_refused(self) -> None:
        books = load_playbooks()
        wrapper = next(item for item in books if item.playbook_id == "private_strategy")
        bent = tuple(
            replace(item, slots=3) if item.playbook_id == SHORT else item for item in books
        )
        with pytest.raises(LegError, match="자리 수"):
            declared_legs(wrapper, bent, scopes(), list(CORE))

    def test_a_plain_bundle_has_no_legs(self) -> None:
        books = load_playbooks()
        plain = next(item for item in books if item.playbook_id == "private_strategy")
        assert declared_legs(plain, books, scopes(), list(CORE)) == ()

    def test_split_legs_without_a_bundle_is_refused(self, tmp_path: Path) -> None:
        raw = yaml.safe_load((ROOT / "config" / "playbooks.yml").read_text(encoding="utf-8"))
        raw["playbooks"]["private_strategy"]["split_legs"] = True
        target = tmp_path / "playbooks.yml"
        target.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        with pytest.raises(PlaybookConfigError, match="split_legs"):
            select.load_playbooks(target)

    def test_an_exposure_above_the_exchange_leverage_is_refused(self, tmp_path: Path) -> None:
        raw = yaml.safe_load((ROOT / "config" / "playbooks.yml").read_text(encoding="utf-8"))
        raw["playbooks"][SHORT]["leg_exposure"] = "5"
        target = tmp_path / "playbooks.yml"
        target.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        with pytest.raises(PlaybookConfigError, match="leg_exposure"):
            select.load_playbooks(target)


class TestEachLegCountsOnlyItsOwnTrades:
    def gate(self, ports: dict[str, FakeSource]) -> LegGate:
        return leg_gate(ports, real_legs(), lambda: Decimal(0))

    def attribution(self, playbook: str) -> str:
        return next(leg.attribution for leg in real_legs() if leg.playbook == playbook)

    def test_a_full_short_leg_does_not_block_the_long_leg(self) -> None:
        short, long = self.attribution(SHORT), self.attribution(LONG)
        ports = {
            symbol: FakeSource(held={short: 1}, size={short: Decimal(2)})
            for symbol in (
                "BNB_USDT",
                "LTC_USDT",
                "LINK_USDT",
                "DOT_USDT",
                "AVAX_USDT",
                "ATOM_USDT",
            )
        }
        ports["BTC_USDT"] = FakeSource()
        gate = self.gate(ports)
        assert gate.grant_for(short, AT, Decimal(2)).blocked == "slots"
        given = gate.grant_for(long, AT, Decimal(4))
        assert given.blocked is None and given.size == Decimal(4), (
            "숏 12x 가 롱의 상한 방을 먹으면 안 된다"
        )

    def test_the_long_leg_still_fits_under_its_own_cap(self) -> None:
        long = self.attribution(LONG)
        ports = {
            symbol: FakeSource(held={long: 1}, size={long: Decimal(4)})
            for symbol in ("ETH_USDT", "XRP_USDT")
        }
        ports["BTC_USDT"] = FakeSource()
        given = self.gate(ports).grant_for(long, AT, Decimal(6))
        assert given.size == Decimal(4) and given.shrunk == "notional"  # 2x6 - 8 = 4

    def test_breadth_counts_only_the_long_legs_symbols(self) -> None:
        long = self.attribution(LONG)
        alts = ("BNB_USDT", "LTC_USDT", "LINK_USDT", "DOT_USDT", "AVAX_USDT")
        ports = {symbol: FakeSource(breaks=1) for symbol in alts}
        ports.update({symbol: FakeSource(breaks=1) for symbol in CORE[:3]})
        gate = self.gate(ports)
        assert gate.legs[long].breadth(AT) == 3, "알트 다섯의 돌파는 폭에 안 든다"

    def test_an_unknown_leg_is_held(self) -> None:
        gate = self.gate({"BTC_USDT": FakeSource()})
        assert gate.grant_for("someone_else@0.1", AT, Decimal(1)).blocked == "leg"
        assert gate.grant(AT, Decimal(1)).blocked == "leg"
        assert gate.blocks(AT) == "leg"

    def test_ports_follow_the_fund_mapping(self) -> None:
        ports: dict[str, FakeSource] = {"BTC_USDT": FakeSource()}
        view = LegPorts(ports, "x", frozenset({"BTC_USDT"}))
        ports["ETH_USDT"] = FakeSource(held={"x": 1})
        assert set(view) == {"BTC_USDT", "ETH_USDT"}
        assert view["ETH_USDT"].open_count() == 1

    def test_a_plain_gate_is_untouched(self) -> None:
        """다리 없는 펀드의 문은 그대로 `grant` 로 묻는다 — 세션이 다리를 알려 줘도 같은 답."""

        class Port:
            def open_count(self) -> int:
                return 0

            def exits(self) -> list[tuple[datetime, bool]]:
                return []

            def open_exposure(self) -> Decimal:
                return Decimal(0)

        gate = SlotGate(ports={"BTC_USDT": Port()}, slots=6)
        assert not hasattr(gate, "grant_for")
        assert gate.grant(AT, Decimal(4)).size == Decimal(4)
