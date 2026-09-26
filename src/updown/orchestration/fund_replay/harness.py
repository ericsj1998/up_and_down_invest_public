"""펀드 재현 걸음 — 판 여러 개 · 한 펀드 · 한 시계 (T309 ② · B).

판마다 실계좌와 같은 `Session`(봉인 피드)을 들고, 펀드는 실계좌와 같은 조정자(`Coordinator` ·
`RebalanceEngine` · `TwrLedger`) · 다리 문(`wire_legs`) · 판 연결(`SessionBridge`)을 쓴다. 한 시간씩
시계를 밀며 그 시각에 걸음이 닿는 판을 (씨앗으로 섞은) 순서대로 걷게 하고, 걸음마다 러너 크기
규칙(`runner_rules`)을 얹는다.

실계좌와 다른 곳(알고 두는 근사 — T309 §4 ② 설계):

- 틱: 실계좌는 4H 경계 + 청산 뒤 **비동기**, 여기는 4H 경계 + 청산 직후 **동기**.
- 지갑: 실계좌는 거래소 지갑 총액에 닻을 내린다. 여기는 원장 실현 손익을 계약 손익으로 맞춘 합.
- 체결: 봉인 걸음 봉의 종가(실계좌 5m) · 거래소 손절 상주 없음(봉이 닿으면) · 재시도 · 정합 없음.
- 러너의 다른 진입 스위치(호가 · 손절 미상주 · 정합 · 가격 괴리)는 재현하지 않는다.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from updown.analysis.indicators.reference import RefNeeds, needs_of, reference_regime
from updown.common.domain.instrument import Timeframe
from updown.decision.allocation import Basket, as_members
from updown.orchestration.fund_replay.runner_rules import (
    ContractSpec,
    add_contracts,
    booked_add,
    dropped_add,
    entry_contracts,
    filled_exposure,
    isolated_margin,
    wallet_adjust,
    with_add,
)
from updown.orchestration.rebalancer.coordinator import Coordinator
from updown.orchestration.rebalancer.engine import RebalanceEngine
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.rebalancer.wiring import wire_legs
from updown.orchestration.walkforward.ledger import Outcome
from updown.portfolio.performance import TwrLedger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle
    from updown.orchestration.rebalancer.legs import FundLeg
    from updown.orchestration.walkforward.session import Session

HOUR = timedelta(hours=1)
FOUR = timedelta(hours=4)
_FRAME_HOURS = {Timeframe.H1: 1, Timeframe.H4: 4}


@dataclass(slots=True)
class ReplayBoard:
    """판 하나 — 세션과 러너 규칙이 기억할 것.

    Attributes:
        symbol: 종목.
        session: 실계좌와 같은 설정의 봉인 세션(`fund_name` · 원장 · 스위치는 조립하는 쪽이 넣는다).
        spec: 거래소 계약 명세.
        funding: `(정산 시각, 요율)` 오름차순 — 러너가 주입하는 `recent_funding` 의 대리값.
        contracts: 매매 id → 진입 계약 수.
        seen: 크기를 정한 진입.
        settled: 계약 손익으로 맞춘 청산.
    """

    symbol: str
    session: Session
    spec: ContractSpec
    funding: Sequence[tuple[datetime, Decimal]] = ()
    contracts: dict[str, int] = field(default_factory=dict[str, int])
    seen: set[str] = field(default_factory=set[str])
    settled: set[str] = field(default_factory=set[str])
    needs: RefNeeds | None = None

    @property
    def hours(self) -> int:
        """걸음 간격(시간) — 세션의 걸음 축."""
        return _FRAME_HOURS[self.session.step_frame]


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """러너 규칙이 한 일 한 줄 (감사 · 대조용).

    Attributes:
        at: 시계 시각.
        symbol: 종목.
        kind: `entry` · `unfillable` · `add` · `add_dropped` · `close` · `margin_exhausted`.
        trade_id: 매매 id.
        detail: 계약 수 · 사유 · 보정액.
    """

    at: datetime
    symbol: str
    kind: str
    trade_id: str
    detail: str


@dataclass(slots=True)
class ReplayResult:
    """한 번 걸은 결과.

    Attributes:
        equity: `(UTC 자정, 펀드 잔고)` — 틱이 본 실현 잔고.
        events: 러너 규칙 사건.
    """

    equity: list[tuple[datetime, Decimal]] = field(default_factory=list[tuple[datetime, Decimal]])
    events: list[ReplayEvent] = field(default_factory=list[ReplayEvent])


def assemble(
    boards: Sequence[ReplayBoard],
    legs: Sequence[FundLeg],
    capital: Decimal,
    slots: int,
) -> Coordinator:
    """판들을 한 펀드로 묶는다 — 실계좌 펀드 생성과 같은 순서.

    `_create_fund` · `_wire_gate` · 첫 틱 순서를 따른다.

    Args:
        boards: 판들(세션 원장은 실계좌처럼 지갑 · 몫 · 예산이 들어 있어야 한다).
        legs: 다리 선언(`declared_legs`).
        capital: 시작 총자본.
        slots: 자리 수(예산 = 총자본 ÷ 자리).

    Returns:
        조정자 — 첫 틱으로 예산을 나눠 준 뒤다. 판은 `fund_ready` 로 풀려 있다.
    """
    basket = Basket(members=as_members([(b.symbol, Decimal(1)) for b in boards]))
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=capital), slots=slots)
    ports: dict[str, SessionBridge] = {b.symbol: SessionBridge(session=b.session) for b in boards}
    coordinator = Coordinator(engine=engine, ports=dict(ports))
    ledger = engine.ledger
    wire_legs(coordinator.ports, legs, lambda: ledger.drawdown_pct / Decimal(100))
    coordinator.tick()
    for b in boards:
        b.session.fund_ready = True
        b.needs = needs_of(b.session.playbooks)
    return coordinator


class FundReplay:
    """판들을 한 시계로 걷는다."""

    def __init__(
        self,
        boards: Sequence[ReplayBoard],
        coordinator: Coordinator,
        ref_4h: Sequence[Candle],
        *,
        seed: int,
    ) -> None:
        """걸음을 준비한다.

        Args:
            boards: 판들(`assemble` 뒤).
            coordinator: 펀드 조정자.
            ref_4h: 기준(BTC) 4H 봉 오름차순 — 국면값을 여기서 낸다.
            seed: 같은 시각 판 순서를 섞는 씨앗(실계좌는 도착 순서).
        """
        self.boards = list(boards)
        self.coordinator = coordinator
        self._ref = list(ref_4h)
        self._ref_ends = [c.ts + FOUR for c in self._ref]
        self._rnd = random.Random(seed)
        self.result = ReplayResult()

    def run(self, start: datetime, end: datetime) -> ReplayResult:
        """`start` 다음 시간부터 `end` 까지 한 시간씩 민다.

        Args:
            start: 판들의 봉인 시작(세션 커서)과 같은 시각(UTC · 정시).
            end: 끝 시각.

        Returns:
            걸은 결과.
        """
        t = start
        while t < end:
            t += HOUR
            if t.hour % 4 == 0:
                self._inject_ref(t)
            self._inject_funding(t)
            due = [b for b in self.boards if t.hour % b.hours == 0]
            self._rnd.shuffle(due)
            for board in due:
                self._halt_if_exhausted(board, t)
                board.session.step()
                self._after_step(board, t)
            if t.hour % 4 == 0:
                self.coordinator.tick()
            if t.hour == 0:
                self.result.equity.append((t, self.coordinator.engine.balance))
        return self.result

    def _halt_if_exhausted(self, board: ReplayBoard, t: datetime) -> None:
        """러너와 같다 — 판 원장이 몫을 다 잃었으면(`halted_at`) 새 진입을 끈다.

        실계좌 `_walk_once` 의 `live_margin_exhausted` 와 같은 규칙이다
        (보유 관리는 계속 · 자동 재개 없음). T312(2026-09-26) 뒤로 펀드 멤버 원장은
        몫으로 `halted_at` 을 세우지 않으므로 여기서 걸리는 것은 단독 판뿐이다.
        """
        s = board.session
        if s.auto and s.ledger.halted_at:
            s.auto = False
            self._note(t, board, "margin_exhausted", str(s.ledger.halted_at), "")

    def _inject_ref(self, t: datetime) -> None:
        """4H 경계마다 기준 봉(마감된 것만)으로 국면값을 넣는다.

        실계좌 `_inject_ref_regime` 과 같은 식(`reference_regime`)이다.
        """
        k = bisect_right(self._ref_ends, t)
        closed = self._ref[:k]
        for board in self.boards:
            needs = board.needs
            if needs is None or needs.empty:
                continue
            s = board.session
            try:
                got = reference_regime(closed, needs)
            except ValueError:
                s.ref_above = s.ref_return = s.ref_surge = s.ref_sma_down = None
                s.ref_vol = ()
                continue
            if needs.ma_n is not None:
                s.ref_above = got.above
            if needs.band_n is not None:
                s.ref_return = got.ret
            if needs.surge_days is not None:
                s.ref_surge = got.surge
            if needs.sma_bars is not None:
                s.ref_sma_down = got.sma_down
            if needs.vol_days is not None:
                s.ref_vol = got.vol

    def _inject_funding(self, t: datetime) -> None:
        """가장 최근 정산 요율을 `recent_funding` 으로 — 러너가 거래소 요율을 넣는 자리."""
        for board in self.boards:
            if not board.funding:
                continue
            k = bisect_right([at for at, _ in board.funding], t) - 1
            if k >= 0:
                board.session.recent_funding = board.funding[k][1]

    def spare(self) -> Decimal:
        """거래소 가용 — 펀드 잔고 - 열린 포지션(원 · 추가)의 격리 증거금 합."""
        held = Decimal(0)
        for board in self.boards:
            led = board.session.ledger
            for rec in led.records:
                if rec.outcome is not Outcome.OPEN:
                    continue
                held += isolated_margin(
                    board.contracts.get(rec.trade_id, 0),
                    rec.entry,
                    board.spec.multiplier,
                    led.leverage,
                )
                if rec.add_contracts > 0 and rec.add_fill is not None:
                    held += isolated_margin(
                        rec.add_contracts, rec.add_fill, board.spec.multiplier, led.leverage
                    )
        return self.coordinator.engine.balance - held

    def _note(self, t: datetime, board: ReplayBoard, kind: str, tid: str, detail: str) -> None:
        self.result.events.append(ReplayEvent(t, board.symbol, kind, tid, detail))

    def _after_step(self, board: ReplayBoard, t: datetime) -> None:
        """러너가 걸음 뒤에 하는 크기 일 — 진입 계약 · 못 사면 거둠 · 불타기 · 청산 손익 맞춤."""
        led = board.session.ledger
        mult = board.spec.multiplier
        closed_any = False
        for tid in [r.trade_id for r in led.records]:
            rec = led.find(tid)
            if rec is None or rec.outcome is Outcome.CANCELLED:
                continue
            if tid not in board.seen and rec.outcome is not Outcome.PENDING:
                board.seen.add(tid)
                n = entry_contracts(
                    rec,
                    budget=led.sizing_base,
                    spare=self.spare(),
                    spec=board.spec,
                    ledger_leverage=led.leverage,
                )
                if n == 0:
                    led.replace(
                        rec.closed(at=rec.placed_at, price=rec.entry, outcome=Outcome.CANCELLED)
                    )
                    if rec.outcome is Outcome.OPEN:
                        board.session.release()
                    self._note(t, board, "unfillable", tid, f"budget {led.sizing_base:.2f}")
                    continue
                board.contracts[tid] = n
                led.replace(
                    dc_replace(
                        rec, filled_leverage=filled_exposure(n, rec.entry, mult, led.sizing_base)
                    )
                )
                self._note(t, board, "entry", tid, f"{n}")
                rec = led.find(tid)
                assert rec is not None
            if (
                rec.outcome is Outcome.OPEN
                and rec.add_exposure > 0
                and rec.add_contracts == 0
                and rec.add_held is None
                and not rec.add_sent
            ):
                n_add, why = add_contracts(
                    rec,
                    sizing_base=led.sizing_base,
                    spare=self.spare(),
                    spec=board.spec,
                    ledger_leverage=led.leverage,
                )
                if why is None:
                    led.replace(with_add(rec, n_add, led.sizing_base, mult))
                    self._note(t, board, "add", tid, f"{n_add}")
                else:
                    led.replace(dropped_add(rec, why))
                    self._note(t, board, "add_dropped", tid, why)
                rec = led.find(tid)
                assert rec is not None
            if (
                rec.closed_at is not None
                and rec.exit_price is not None
                and tid not in board.settled
            ):
                board.settled.add(tid)
                adj = wallet_adjust(rec, board.contracts.get(tid, 0), mult, led.leverage)
                led.replace(
                    booked_add(dc_replace(rec, realized_adjust=rec.realized_adjust + adj), mult)
                )
                self._note(t, board, "close", tid, f"{adj:.4f}")
                closed_any = True
        if closed_any:
            self.coordinator.tick()


def utc_hour(moment: datetime) -> datetime:
    """UTC 정시로 내린다 — 시계 시작 시각을 정할 때."""
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
