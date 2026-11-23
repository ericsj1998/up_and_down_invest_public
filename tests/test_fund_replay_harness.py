"""펀드 재현 걸음(T309 ②) — 실제 원장 · 조정자 · 다리 문 위에서 각본 세션으로 본다.

러너 규칙(계약 · 못 사면 거둠 · 계약 손익)과 틱(예산 · 잔고)을 확인한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.common.domain.instrument import Timeframe
from updown.common.domain.releases import ReleaseSchedule
from updown.orchestration.fund_replay.harness import (
    FundReplay,
    ReplayBoard,
    ReplayInputError,
    assemble,
    marked_pnl,
)
from updown.orchestration.fund_replay.runner_rules import ContractSpec
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Funding,
    Ledger,
    Outcome,
    TradeRecord,
)

START = datetime(2026, 1, 1, 20, tzinfo=UTC)
COST = Decimal("0.0016")


class ScriptSession:
    """각본대로 매매를 여닫는 세션 — 원장은 실계좌 펀드 판과 같은 설정."""

    def __init__(self, symbol: str, script: dict[int, tuple[str, Decimal]]) -> None:
        self.instrument = SimpleNamespace(symbol=symbol)
        self.step_frame = Timeframe.H1
        self.playbooks: list[Any] = []
        self.fund_ready = False
        self.fund_name = "fund"
        self.reconciled = True
        self.accounting_ok = True
        self.verified_realized = None
        self.entry_gate: Any = None
        self.leg_leverage: Any = None
        self.recent_funding: Decimal | None = None
        self.ref_above = self.ref_return = self.ref_surge = self.ref_sma_down = None
        self.ref_vol: tuple[Any, ...] = ()
        self.ledger = Ledger(
            funding=Funding.WALLET,
            wallet_start=Decimal(0),
            seed_cash=Decimal(500),
            leverage=Decimal(6),
            refill=False,
        )
        self.released = 0
        self.auto = True
        self._script = script
        self._n = 0
        self.cursor = START
        self.last_price: Decimal | None = None
        # 평가 잔고(T971)가 읽는 두 면 — 마지막 닫힌 봉 종가 · 보유 몫
        self.feed = SimpleNamespace(judged=self._judged)

    def _judged(self, _frame: Timeframe) -> list[SimpleNamespace]:
        return [] if self.last_price is None else [SimpleNamespace(close=self.last_price)]

    @property
    def positions(self) -> tuple[TradeRecord, ...]:
        return tuple(r for r in self.ledger.records if r.outcome is Outcome.OPEN)

    def release(self) -> None:
        self.released += 1

    def step(self) -> None:
        self._n += 1
        self.cursor += timedelta(hours=1)
        act = self._script.get(self._n)
        if act is None:
            return
        kind, price = act
        self.last_price = price
        if kind == "mark":  # 값만 움직인다(매매 없음)
            return
        if kind == "open":
            self.ledger.add(
                TradeRecord(
                    trade_id=f"{self.instrument.symbol}-1",
                    playbook="p@0.1.0",
                    actor=Actor.SYSTEM,
                    placed_at=self.cursor,
                    opened_at=self.cursor,
                    entry=price,
                    direction=Direction.LONG,
                    outcome=Outcome.OPEN,
                    leverage=Decimal(4),
                    cost_pct=COST,
                )
            )
        else:
            rec = self.ledger.find(f"{self.instrument.symbol}-1")
            assert rec is not None
            self.ledger.replace(
                rec.closed(at=self.cursor, price=price, outcome=Outcome.SIGNAL_EXIT)
            )


def build(seed: int = 1) -> tuple[FundReplay, ScriptSession, ScriptSession]:
    a = ScriptSession("AAA_USDT", {1: ("open", Decimal(130)), 3: ("close", Decimal(143))})
    b = ScriptSession("BBB_USDT", {1: ("open", Decimal(5000))})
    boards = [
        ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1))),
        ReplayBoard(symbol="BBB_USDT", session=cast("Any", b), spec=ContractSpec(Decimal(1))),
    ]
    coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
    return FundReplay(boards, coordinator, ref_4h=(), seed=seed), a, b


class TestReplay:
    def test_first_tick_hands_out_slot_budgets_and_releases_the_boards(self) -> None:
        _replay, a, b = build()
        assert a.ledger.margin_budget == Decimal(500) == b.ledger.margin_budget
        assert a.fund_ready and b.fund_ready

    def test_entry_is_sized_in_contracts_and_the_close_books_contract_pnl(self) -> None:
        replay, a, _b = build()
        result = replay.run(START, START + timedelta(hours=6))
        # 쓸 돈 min(500, 가용 1000) x 0.99 = 495 · 노출 4 → 1980 ÷ 130 = 15.2 → 15 계약(명목 1950)
        rec = a.ledger.find("AAA_USDT-1")
        assert rec is not None and rec.filled_leverage == Decimal(1950) / Decimal(500)
        # 계약 손익 = 1950 x (0.10 - 0.0016) — 원장(의도 명목 2000)과의 차이를 보정해 지갑에 닻
        want = Decimal(1950) * (Decimal("0.1") - COST)
        assert abs(a.ledger.realized_cash - want) < Decimal("1e-9")
        assert abs(replay.coordinator.engine.balance - (Decimal(1000) + want)) < Decimal("1e-9")
        kinds = [(e.symbol, e.kind) for e in result.events]
        assert ("AAA_USDT", "entry") in kinds and ("AAA_USDT", "close") in kinds

    def test_an_entry_that_cannot_buy_one_contract_is_cancelled_and_released(self) -> None:
        replay, _a, b = build()
        result = replay.run(START, START + timedelta(hours=2))
        rec = b.ledger.find("BBB_USDT-1")
        assert rec is not None and rec.outcome is Outcome.CANCELLED
        assert b.released == 1
        assert ("BBB_USDT", "unfillable") in [(e.symbol, e.kind) for e in result.events]

    def test_same_seed_same_walk(self) -> None:
        one, _, _ = build(seed=7)
        two, _, _ = build(seed=7)
        r1 = one.run(START, START + timedelta(hours=6))
        r2 = two.run(START, START + timedelta(hours=6))
        assert [(e.at, e.symbol, e.kind, e.detail) for e in r1.events] == [
            (e.at, e.symbol, e.kind, e.detail) for e in r2.events
        ]
        assert r1.equity == r2.equity

    def test_a_board_that_lost_its_share_keeps_trading(self) -> None:
        """T312 — 몫(500)을 넘게 잃어도 펀드 판은 안 멈춘다.

        원장에 `halted_at` 이 안 서고 `auto` 도 그대로다.
        """
        a = ScriptSession("AAA_USDT", {1: ("open", Decimal(130)), 3: ("close", Decimal(40))})
        boards = [
            ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1)))
        ]
        coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
        result = FundReplay(boards, coordinator, ref_4h=(), seed=1).run(
            START, START + timedelta(hours=6)
        )
        assert a.ledger.realized_cash < -Decimal(500)
        assert a.ledger.halted_at is None
        assert a.auto is True
        assert ("AAA_USDT", "margin_exhausted") not in [(e.symbol, e.kind) for e in result.events]

    def test_daily_equity_is_recorded_at_utc_midnight(self) -> None:
        replay, _a, _b = build()
        result = replay.run(START, START + timedelta(hours=6))
        assert [t for t, _ in result.equity] == [datetime(2026, 1, 2, tzinfo=UTC)]


class TestMarkToMarket:
    """T971 — 자정 평가 잔고 = 실현 잔고 + 열린 매매 평가 손익(연구 엔진의 평가 MDD 와 같은 자)."""

    def test_flat_book_marks_the_same_as_realized(self) -> None:
        replay, _a, _b = build()
        result = replay.run(START, START + timedelta(hours=6))
        assert result.mtm == result.equity  # 자정에 열린 매매가 없다

    def test_an_open_trade_is_marked_at_the_last_closed_bar(self) -> None:
        # 1 걸음 130 에 15 계약(실제 노출 1950 ÷ 500) · 자정(4 걸음) 전 3 걸음에 값만 117(-10%)
        a = ScriptSession("AAA_USDT", {1: ("open", Decimal(130)), 3: ("mark", Decimal(117))})
        boards = [
            ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1)))
        ]
        coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
        result = FundReplay(boards, coordinator, ref_4h=(), seed=1).run(
            START, START + timedelta(hours=6)
        )
        (_, realized), (_, marked) = result.equity[0], result.mtm[0]
        # 1950 명목 x -10% = -195 — 실현 잔고는 안 움직이고 평가 잔고만 내려간다
        assert realized == Decimal(1000)
        assert abs(marked - (Decimal(1000) - Decimal(195))) < Decimal("1e-9")


class TestMarkedPnl:
    @staticmethod
    def rec(**kw: Any) -> TradeRecord:
        base: dict[str, Any] = {
            "trade_id": "t",
            "playbook": "p@0.1.0",
            "actor": Actor.SYSTEM,
            "placed_at": START,
            "opened_at": START,
            "entry": Decimal(100),
            "direction": Direction.LONG,
            "outcome": Outcome.OPEN,
            "leverage": Decimal(3),
            "margin_used": Decimal(100),
        }
        base.update(kw)
        return TradeRecord(**base)

    def test_long_short_and_unknown_margin(self) -> None:
        assert marked_pnl(self.rec(), Decimal(110)) == Decimal(30)
        assert marked_pnl(self.rec(direction=Direction.SHORT), Decimal(90)) == Decimal(30)
        assert marked_pnl(self.rec(margin_used=None), Decimal(110)) == Decimal(0)

    def test_filled_exposure_wins_over_intent(self) -> None:
        """재현은 계약으로 채운 노출을 지갑에 닻 내린다 — 같은 자로 평가한다."""
        got = marked_pnl(self.rec(filled_leverage=Decimal("2.5")), Decimal(110))
        assert got == Decimal(25)

    def test_half_taken_and_pyramid_add_and_booked_adjust(self) -> None:
        half = self.rec(half_at=START, half_price=Decimal(106))
        assert marked_pnl(half, Decimal(110)) == Decimal(24)  # 평균 108 → 100 x 3 x 8%
        add = self.rec(add_exposure=Decimal("1.5"), add_price=Decimal(105))
        want = Decimal(30) + Decimal(100) * Decimal("1.5") * Decimal(5) / Decimal(105)
        assert abs(marked_pnl(add, Decimal(110)) - want) < Decimal("1e-12")
        assert marked_pnl(self.rec(realized_adjust=Decimal(-4)), Decimal(100)) == Decimal(-4)


class MemberSession(ScriptSession):
    """분기 우주 각본 세션 — 범위가 비고 보유가 없으면 커서만(`Session.idle_advance` 꼴)."""

    def __init__(self, symbol: str, script: dict[int, tuple[str, Decimal]]) -> None:
        super().__init__(symbol, script)
        self.entry_legs: frozenset[str] | None = None
        self.idled = 0
        self.seen_legs: list[frozenset[str] | None] = []

    def idle_advance(self) -> bool:
        if self.entry_legs is None or self.entry_legs:
            return False
        self.idled += 1
        self.cursor += timedelta(hours=1)
        return True

    def step(self) -> None:
        self.seen_legs.append(self.entry_legs)
        super().step()


class TestMembership:
    """T786 — 분기 우주 펀드 재현: 세션 진입 범위를 시각마다 걸고, 범위가 빈 판은 판단 없이 민다."""

    def test_entry_legs_follow_the_quarter_and_empty_boards_only_advance(self) -> None:
        a = MemberSession("AAA_USDT", {})
        b = MemberSession("BBB_USDT", {})
        boards = [
            ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1))),
            ReplayBoard(symbol="BBB_USDT", session=cast("Any", b), spec=ContractSpec(Decimal(1))),
        ]
        coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
        switch = START + timedelta(hours=3)

        def member(symbol: str, at: datetime) -> frozenset[str] | None:
            if symbol == "AAA_USDT":
                return frozenset({"leg_a"})
            return frozenset() if at <= switch else frozenset({"leg_b"})

        FundReplay(boards, coordinator, ref_4h=(), seed=1, membership=member).run(
            START, START + timedelta(hours=6)
        )
        assert a.idled == 0 and len(a.seen_legs) == 6
        assert set(a.seen_legs) == {frozenset({"leg_a"})}
        assert b.idled == 3 and len(b.seen_legs) == 3  # 범위가 빈 세 걸음은 판단(step) 없이 커서만
        assert set(b.seen_legs) == {frozenset({"leg_b"})}

    def test_without_membership_nothing_changes(self) -> None:
        a = MemberSession("AAA_USDT", {})
        boards = [
            ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1)))
        ]
        coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
        FundReplay(boards, coordinator, ref_4h=(), seed=1).run(START, START + timedelta(hours=4))
        assert a.idled == 0 and len(a.seen_legs) == 4 and set(a.seen_legs) == {None}


class TestInputCheck:
    """T968 — 판이 선언한 진입 문의 입력이 빠지면 걷기 전에 멈춘다(모름 = 보류로 안 돎)."""

    @staticmethod
    def _boards() -> tuple[list[ReplayBoard], Any]:
        a = ScriptSession("AAA_USDT", {})
        boards = [
            ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1)))
        ]
        coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
        return boards, coordinator

    def test_declared_gates_need_their_inputs(self) -> None:
        boards, coordinator = self._boards()
        cast("Any", boards[0].session).playbooks = [
            SimpleNamespace(
                entry_event_window=SimpleNamespace(hours=Decimal(8)),
                entry_washed=SimpleNamespace(),
                drawdown_brake=None,
            )
        ]
        with pytest.raises(ReplayInputError, match=r"발표 일정.*시장 전체 값"):
            FundReplay(boards, coordinator, ref_4h=(), seed=1)
        # 옛 재현과 맞대기는 모름을 허용한다
        FundReplay(boards, coordinator, ref_4h=(), seed=1, allow_unknown_inputs=True)

    def test_brake_release_needs_market_values(self) -> None:
        # T985 — 브레이크 풀기를 선언했는데 시장 폭이 없으면 "모름 = 안 푼다" 로 조용히 돌지 않는다.
        boards, coordinator = self._boards()
        cast("Any", boards[0].session).playbooks = [
            SimpleNamespace(
                entry_event_window=None,
                entry_washed=None,
                drawdown_brake=SimpleNamespace(release=SimpleNamespace()),
            )
        ]
        with pytest.raises(ReplayInputError, match=r"drawdown_brake\.release"):
            FundReplay(boards, coordinator, ref_4h=(), seed=1)

    def test_schedule_must_cover_the_window(self) -> None:
        boards, coordinator = self._boards()
        cast("Any", boards[0].session).playbooks = [
            SimpleNamespace(
                entry_event_window=SimpleNamespace(hours=Decimal(8)),
                entry_washed=None,
                drawdown_brake=None,
            )
        ]
        short = ReleaseSchedule(since=START, horizon=START + timedelta(days=1), times={})
        replay = FundReplay(boards, coordinator, ref_4h=(), seed=1, release_schedule=short)
        with pytest.raises(ReplayInputError, match="창을 못 덮음"):
            replay.run(START, START + timedelta(hours=4))

    def test_reference_bars_must_cover_the_needs(self) -> None:
        boards, coordinator = self._boards()
        boards[0].needs = cast("Any", SimpleNamespace(bars=10, empty=False))
        with pytest.raises(ReplayInputError, match="기준 4H 봉 0 개"):
            FundReplay(boards, coordinator, ref_4h=(), seed=1).run(
                START, START + timedelta(hours=4)
            )

    def test_no_declared_gates_walks_as_before(self) -> None:
        boards, coordinator = self._boards()
        FundReplay(boards, coordinator, ref_4h=(), seed=1).run(START, START + timedelta(hours=4))
