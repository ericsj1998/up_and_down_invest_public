"""펀드 자동 앵커 (T285 · 2026-09-17) — 총자본을 거래소 계좌에 스스로 맞춘다.

막아야 하는 실패:
1. 🔴 장부가 샜는데(132) 계좌(298)를 읽고도 안 고치는 것 · 고치면서 TWR 을 못 돌려놓는 것.
2. 🔴 거래소 입금이 성과로 둔갑하는 것 (계좌 모드는 흐름으로 · 드리프트 모드는 유휴 현금으로).
3. 🔴 같은 입출금 행을 두 번 세는 것 (재시작 · 같은 행이 다시 옴).
4. 🔴 테스트넷 1만 USDT 에 1,000 짜리 데모를 계좌 전체로 부풀리는 것 (드리프트 모드).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.orchestration.rebalancer.anchor import (
    MODE_ACCOUNT,
    MODE_DRIFT,
    AnchorState,
    anchored,
    choose_mode,
    dnw_since,
    initial_state,
    manual_flow,
    row_time,
)
from updown.portfolio.performance import CashFlow, TwrLedger

T0 = datetime(2026, 9, 17, 12, tzinfo=UTC)


def gate_row(kind: str, change: str, at: datetime) -> dict[str, object]:
    return {"type": kind, "change": change, "time": str(at.timestamp()), "text": ""}


def binance_row(kind: str, change: str, at: datetime) -> dict[str, object]:
    return {"type": kind, "change": change, "time": str(int(at.timestamp() * 1000)), "text": ""}


class TestMode:
    def test_account_when_the_account_is_the_fund(self) -> None:
        """실계좌: 투입 300 · 계좌 298 → 계좌가 곧 펀드."""
        assert choose_mode(Decimal("298.09"), Decimal(300)) == MODE_ACCOUNT

    def test_drift_when_the_account_is_much_bigger(self) -> None:
        """테스트넷 1만에 1,000 짜리 데모 → 펀드 밖 유휴 현금."""
        state = initial_state(Decimal(10078), Decimal(1000), Decimal(1000))
        assert state.mode == MODE_DRIFT
        assert state.idle == Decimal(9078)

    def test_account_mode_has_no_idle(self) -> None:
        state = initial_state(Decimal("298.09"), Decimal(300), Decimal("131.7"))
        assert state.mode == MODE_ACCOUNT
        assert state.idle == 0


class TestDnw:
    def test_gate_and_binance_rows_and_seen_keys(self) -> None:
        rows = [
            gate_row("dnw", "50", T0 + timedelta(minutes=5)),
            gate_row("pnl", "3", T0 + timedelta(minutes=6)),  # 손익은 입출금이 아니다
            binance_row("TRANSFER", "-20", T0 + timedelta(minutes=7)),
            gate_row("dnw", "999", T0 - timedelta(hours=1)),  # 앵커 전 — 안 센다
        ]
        total, seen = dnw_since(rows, T0, ())
        assert total == Decimal(30)
        assert len(seen) == 2
        again, seen2 = dnw_since(rows, T0, seen)  # 같은 행이 다시 와도 두 번 안 센다
        assert again == 0
        assert seen2 == seen

    def test_row_time_handles_seconds_and_millis(self) -> None:
        assert row_time("1789000000.5") is not None
        assert row_time("1789000000500") == row_time("1789000000.5")
        assert row_time("") is None


class TestAnchored:
    def test_account_mode_treats_dnw_as_flow(self) -> None:
        state = AnchorState(mode=MODE_ACCOUNT)
        got = anchored(state, Decimal(350), Decimal(50), T0)  # 입금 50 이 방금 들어왔다
        assert got.equity_before_flow == Decimal(300)
        assert got.flow == Decimal(50)
        assert got.state.at == T0

    def test_drift_mode_sends_dnw_to_idle(self) -> None:
        state = AnchorState(mode=MODE_DRIFT, idle=Decimal(9000))
        got = anchored(state, Decimal(10100), Decimal(100), T0)  # 테스트넷에 100 더 넣었다
        assert got.equity_before_flow == Decimal(1000)  # 펀드 몫은 그대로
        assert got.flow == 0
        assert got.state.idle == Decimal(9100)

    def test_manual_flow_moves_idle_into_the_fund(self) -> None:
        state = AnchorState(mode=MODE_DRIFT, idle=Decimal(9000))
        assert manual_flow(state, Decimal(500)).idle == Decimal(8500)
        assert manual_flow(AnchorState(mode=MODE_ACCOUNT), Decimal(500)).idle == 0


class _Port:
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol
        self.budget: Decimal | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    def realized(self) -> Decimal:
        return Decimal(0)

    def set_budget(self, budget: Decimal) -> None:
        self.budget = budget


class TestCoordinatorWithAnchor:
    def _coord(self, balance: str, index: str) -> Coordinator:
        ledger = TwrLedger(equity=Decimal(balance), contributed=Decimal(300), _twr=Decimal(index))
        basket = Basket(as_members([("BTC_USDT", Decimal(1)), ("ETH_USDT", Decimal(1))]))
        engine = RebalanceEngine(basket=basket, ledger=ledger)
        return Coordinator(
            engine=engine, ports={"BTC_USDT": _Port("BTC_USDT"), "ETH_USDT": _Port("ETH_USDT")}
        )  # type: ignore[arg-type]

    def test_leaked_ledger_snaps_to_the_account_and_twr_heals(self) -> None:
        """🔴 실계좌 재현: 장부 132(누수) · 지수 0.439 → 계좌 298 로 앵커 → 누적 -0.6%."""
        coord = self._coord("131.7", "0.439")
        report = coord.tick(anchor=Decimal("298.09"))
        assert report.balance == Decimal("298.09")
        assert abs(report.twr_return - Decimal("-0.0064")) < Decimal("0.001")

    def test_anchor_with_flow_keeps_the_deposit_out_of_performance(self) -> None:
        coord = self._coord("300", "1")
        flow = CashFlow(at=T0, amount=Decimal(50), note="거래소 입출금 자동 반영")
        report = coord.tick(flow, anchor=Decimal(300))  # 계좌 350 = 300 + 입금 50
        assert report.balance == Decimal(350)
        assert report.twr_return == 0

    def test_no_anchor_falls_back_to_increments(self) -> None:
        coord = self._coord("300", "1")
        assert coord.tick().balance == Decimal(300)
