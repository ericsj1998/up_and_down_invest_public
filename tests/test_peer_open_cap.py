"""펀드 문의 **짝 다리 보유 문턱** (512차 N4 · T320 · 2026-09-28).

512차: 일봉 채널 새 다리는 1H 돌파 롱이 이미 4개 이상 열려 있으면 건너뛴다 — 그 자리의 새 다리는
건당 음수였고(알트가 한꺼번에 오른 뒤의 늦은 돌파), 이웃 문턱 2 · 3개와 함께 기준 대비 ✅(N4f).
여기서 못 박는 것 — 연구와 같은 부등호(`>=`)인가 · 짝 다리의 보유를 펀드 전체에서 세는가 ·
선언이 저장 · 되살림을 지나는가.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks, peer_open_cap
from updown.analysis.playbook.types import PeerOpenCap
from updown.decision.portfolio_rules import peer_crowded
from updown.orchestration.rebalancer.legs import FundLeg, LegError, declared_legs, leg_gate

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 9, 28, 12, tzinfo=UTC)
LONG = "private_strategy"
SHORT = "private_strategy"
CORE = ("BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT")


def scopes() -> dict[str, list[str]]:
    raw = yaml.safe_load((ROOT / "config" / "baskets.yml").read_text(encoding="utf-8"))
    return {
        name: [str(row["symbol"]) for row in body["members"]]
        for name, body in raw["by_playbook"].items()
    }


def legs_with_peer(cap: PeerOpenCap) -> tuple[tuple[FundLeg, ...], str, str]:
    """`private_strategy` 묶음의 숏 다리에 롱 다리 문턱을 단 다리들 — 규칙의 배선만 본다."""
    books = load_playbooks()
    wrapper = next(item for item in books if item.playbook_id == "private_strategy")
    bent = [
        replace(item, entry_peer_open_max=cap) if item.playbook_id == SHORT else item
        for item in books
    ]
    legs = declared_legs(wrapper, bent, scopes(), scopes()["private_strategy"])
    long_attr = next(item.attribution for item in books if item.playbook_id == LONG)
    short_attr = next(item.attribution for item in books if item.playbook_id == SHORT)
    return legs, long_attr, short_attr


@dataclass
class FakeSource:
    """`LegSource` — 다리별 열린 수를 손으로 심는다."""

    held: dict[str, int] = field(default_factory=dict[str, int])

    def open_count_of(self, leg: str) -> int:
        return self.held.get(leg, 0)

    def exits_of(self, leg: str) -> list[tuple[datetime, bool]]:  # noqa: ARG002
        return []

    def open_exposure_of(self, leg: str) -> Decimal:  # noqa: ARG002
        return Decimal(0)

    def band_breaks(self, at: datetime) -> int:  # noqa: ARG002
        return 0


class TestRule:
    def test_same_inequality_as_research(self) -> None:
        """연구 원장 `long_n >= 4` 면 건너뜀."""
        assert not peer_crowded(3, 4)
        assert peer_crowded(4, 4)
        assert peer_crowded(6, 4)

    def test_zero_means_off(self) -> None:
        assert not peer_crowded(99, 0)


class TestDeclaration:
    def test_parses(self) -> None:
        assert peer_open_cap({"leg": LONG, "count": 4}, "t") == PeerOpenCap(leg=LONG, count=4)

    @pytest.mark.parametrize(
        "raw", [{"leg": LONG, "count": 0}, {"count": 4}, {"leg": "", "count": 4}]
    )
    def test_rejects_nonsense(self, raw: dict[str, object]) -> None:
        with pytest.raises(PlaybookConfigError):
            peer_open_cap(raw, "t")

    def test_leg_id_becomes_the_peer_attribution(self) -> None:
        legs, long_attr, short_attr = legs_with_peer(PeerOpenCap(leg=LONG, count=4))
        short = next(leg for leg in legs if leg.attribution == short_attr)
        assert short.peer_open_max == (long_attr, 4)

    def test_peer_must_be_another_member_of_the_bundle(self) -> None:
        with pytest.raises(LegError, match="같은 묶음"):
            legs_with_peer(PeerOpenCap(leg="nope", count=4))
        with pytest.raises(LegError, match="같은 묶음"):
            legs_with_peer(PeerOpenCap(leg=SHORT, count=4))

    def test_round_trip_and_old_saves(self) -> None:
        legs, _long, short_attr = legs_with_peer(PeerOpenCap(leg=LONG, count=4))
        short = next(leg for leg in legs if leg.attribution == short_attr)
        assert FundLeg.from_dict(short.to_dict()) == short
        old = short.to_dict()
        old.pop("peer_open_max")
        assert FundLeg.from_dict(old).peer_open_max is None, "옛 저장본엔 없다 — 끔"


class TestGate:
    def gate_for(self, held_longs: int) -> str | None:
        legs, long_attr, short_attr = legs_with_peer(PeerOpenCap(leg=LONG, count=4))
        ports = {
            symbol: FakeSource(held={long_attr: 1 if i < held_longs else 0})
            for i, symbol in enumerate(CORE)
        }
        gate = leg_gate(ports, legs, lambda: Decimal(0))
        return gate.grant_for(short_attr, AT, Decimal("0.1")).blocked

    def test_three_peers_open_passes(self) -> None:
        assert self.gate_for(3) is None

    def test_four_peers_open_blocks_fund_wide(self) -> None:
        """🔴 짝 다리 보유를 **펀드 전체**에서 센다 — 종목마다 하나씩 네 종목."""
        assert self.gate_for(4) == "peer_open"

    def test_the_peer_leg_itself_is_not_gated(self) -> None:
        legs, long_attr, _short = legs_with_peer(PeerOpenCap(leg=LONG, count=4))
        ports = {symbol: FakeSource(held={long_attr: 0}) for symbol in CORE}
        gate = leg_gate(ports, legs, lambda: Decimal(0))
        assert gate.grant_for(long_attr, AT, Decimal("0.1")).blocked is None
