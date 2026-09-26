"""돌파 롱 크기 x1.2 (2026-09-26 · 사용자 "일단 이걸로 해주라") — 선언과 증거금 맞춤을 잠근다.

- 크기는 **변동성 목표 쪽**에 건다: 펀드 다리 노출(4)은 돌던 펀드의 저장본이 쥐고 있어
  선언을 바꿔도 안 바뀐다. 배수 = clip(scale ÷ 변동성, low, high) 라 셋 다 x1.2 면 배수가
  정확히 x1.2 다.
- 노출이 거래소 배율을 넘으면(4 x 1.8 = 7.2 > 6) 증거금이 쓸 돈보다 커진다 —
  가용 안으로 계약을 줄인다.
  실계좌 `_send` 와 펀드 재현 `entry_contracts` 가 같은 함수(`fit_to_margin`)를 부른다.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import Playbook
from updown.orchestration.fund_replay.runner_rules import ContractSpec, entry_contracts
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import MARGIN_HEADROOM, LiveRunner
from updown.orchestration.walkforward.order_mapping import fit_to_margin

LONG = "private_strategy"
TRI = "private_strategy"
MACD = "private_strategy"


def books() -> dict[str, Playbook]:
    return {b.playbook_id: b for b in load_playbooks()}


class TestDeclaration:
    def test_the_long_leg_is_sized_up_by_exactly_a_fifth(self) -> None:
        """배수가 모든 변동성에서 정확히 x1.2 — 상 · 하한에 걸린 자리까지."""
        got = books()
        long_t = got[LONG].entry_vol_target
        tri_t = got[TRI].entry_vol_target
        assert long_t is not None and tri_t is not None
        for sigma in ("0.15", "0.25", "0.3", "0.4276", "0.6", "0.9", "1.5"):
            s = Decimal(sigma)
            assert abs(long_t.mult(s) - tri_t.mult(s) * Decimal("1.2")) < Decimal("1e-20"), sigma

    def test_the_short_legs_keep_the_old_target(self) -> None:
        got = books()
        for name in (TRI, MACD):
            t = got[name].entry_vol_target
            assert t is not None
            assert (t.scale, t.low, t.high, t.days) == (
                Decimal("0.4275650014064095"),
                Decimal("0.5"),
                Decimal("1.5"),
                30,
            ), name

    def test_the_running_legs_keep_their_version_and_exposure(self) -> None:
        """🔴 버전을 올리면 펀드 문이 모든 진입을 `leg` 로 막는다 · 노출은 펀드 저장본이 쥔다."""
        got = books()
        assert got[LONG].version == "0.1.0"
        assert got[LONG].leg_exposure == Decimal(4)
        assert got[LONG].leverage == Decimal(6)


class TestFitToMargin:
    def test_room_enough_keeps_the_count(self) -> None:
        # 10계약 x 100 x 1 ÷ 6 = 166.7 ≤ 200
        assert fit_to_margin(10, Decimal(100), Decimal(1), Decimal(6), Decimal(200)) == 10

    def test_too_little_room_trims_down(self) -> None:
        # 가용 120 x 6 ÷ 100 = 7.2 → 7계약
        assert fit_to_margin(10, Decimal(100), Decimal(1), Decimal(6), Decimal(120)) == 7

    def test_unknown_spare_does_not_trim(self) -> None:
        assert fit_to_margin(10, Decimal(100), Decimal(1), Decimal(6), None) == 10

    def test_no_room_gives_zero(self) -> None:
        assert fit_to_margin(10, Decimal(100), Decimal(1), Decimal(6), Decimal(0)) == 0


def record(exposure: str) -> TradeRecord:
    return TradeRecord(
        trade_id="t1",
        playbook=f"{LONG}@0.1.0",
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=datetime(2026, 9, 26, tzinfo=UTC),
        entry=Decimal(100),
        planned_stop=Decimal(95),
        planned_first=Decimal(110),
        planned_target=Decimal(120),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.001"),
        leverage=Decimal(exposure),
    )


class TestReplaySizing:
    SPEC = ContractSpec(multiplier=Decimal(1))

    def test_an_exposure_above_the_exchange_leverage_is_fitted_to_the_spare(self) -> None:
        """노출 7.2 · 배율 6 · 예산 · 가용 100 → 원하던 7계약(증거금 120)이 5계약으로."""
        got = entry_contracts(
            record("7.2"),
            budget=Decimal(100),
            spare=Decimal(100),
            spec=self.SPEC,
            ledger_leverage=Decimal(6),
        )
        assert got * Decimal(100) / Decimal(6) <= Decimal(100) * MARGIN_HEADROOM
        assert got == 5

    def test_a_roomy_wallet_buys_the_full_size(self) -> None:
        got = entry_contracts(
            record("7.2"),
            budget=Decimal(100),
            spare=Decimal(1000),
            spec=self.SPEC,
            ledger_leverage=Decimal(6),
        )
        assert got == 7  # 99 x 7.2 ÷ 100 = 7.1 → 반올림 7

    def test_the_old_size_is_untouched_when_the_wallet_has_room(self) -> None:
        """노출 ≤ 배율이고 가용이 넉넉하면 맞춤이 한 계약도 안 바꾼다 — 지금까지의 판과 같다."""
        got = entry_contracts(
            record("6"),
            budget=Decimal(100),
            spare=Decimal(1000),
            spec=self.SPEC,
            ledger_leverage=Decimal(6),
        )
        assert got == 6  # 99 x 6 ÷ 100 = 5.94 → 반올림 6

    def test_a_round_up_past_the_spare_is_taken_back(self) -> None:
        """⚠️ 가용이 딱 예산만큼이면 반올림 한 계약이 증거금을 가용 위로 민다 — 한 계약 덜.

        거래소는 잔액에 딱 맞춘 증거금도 거절한다(표시가 · 수수료 차).
        """
        got = entry_contracts(
            record("6"),
            budget=Decimal(100),
            spare=Decimal(100),
            spec=self.SPEC,
            ledger_leverage=Decimal(6),
        )
        assert got == 5


@pytest.mark.parametrize("needle", ["fit_to_margin", "_skip_unfillable", "live_entry_fit_margin"])
def test_the_live_send_fits_the_margin(needle: str) -> None:
    """🔴 한쪽에만 있는 안전장치는 언젠가 다른 쪽에서 사고가 난다 — 실계좌 시장가 경로."""
    assert needle in inspect.getsource(LiveRunner._send)  # pyright: ignore[reportPrivateUsage]


def test_the_spare_seen_is_remembered() -> None:
    assert "self._entry_spare = spare" in inspect.getsource(LiveRunner._usable_equity)  # pyright: ignore[reportPrivateUsage]
