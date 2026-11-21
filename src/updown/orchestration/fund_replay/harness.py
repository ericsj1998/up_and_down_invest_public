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

import asyncio
import random
from bisect import bisect_right
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from updown.analysis.indicators.reference import RefNeeds, needs_of, reference_regime
from updown.analysis.playbook.types import WashedRule
from updown.common.domain.instrument import Timeframe
from updown.common.domain.releases import ReleaseSchedule
from updown.decision.allocation import Basket, as_members
from updown.orchestration.fund_replay.runner_rules import (
    ContractSpec,
    add_contracts,
    add_wanted_spare,
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
from updown.orchestration.rebalancer.legs import isolated_attributions
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.rebalancer.wiring import wire_legs
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.ref_inputs import (
    apply_ref_regime,
    clear_ref_regime,
    market_day_of,
    washed_rule_of,
)
from updown.portfolio.performance import TwrLedger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle
    from updown.orchestration.fund_replay.engine_leg import ReplayEngineLeg
    from updown.orchestration.rebalancer.legs import FundLeg
    from updown.orchestration.walkforward.ledger import TradeRecord
    from updown.orchestration.walkforward.session import Session

HOUR = timedelta(hours=1)
FOUR = timedelta(hours=4)


def marked_pnl(record: TradeRecord, mark: Decimal) -> Decimal:
    """열린 매매 하나의 **평가 손익**(USDT · 비용 · 펀딩 전) — 걷기 식을 지금 값으로 (T971).

    Args:
        record: 보유 중인 매매(`margin_used` 가 있는 펀드 멤버 기록).
        mark: 지금 값(그 판의 마지막 닫힌 봉 종가).

    Returns:
        `margin_used x 실제 노출 x 값 변화율` + 불타기 추가분 + 이미 실현된 보정.
        실제 노출 = 체결이 만든 노출(`filled_leverage` · 재현은 계약으로 채운다) · 없으면
        `채운 비율 x 배율`(원장 의도 식). `margin_used` 가 없거나 값이 0 이하면 0.

    Note:
        청산 때 재현이 지갑에 닻 내리는 값은 계약 손익(= 실제 노출)이라 같은 자로 평가한다 — 청산
        순간 평가 ≈ 실현(비용 · 펀딩만 다르다). 반익을 덜었으면 그 절반은 덜어 낸 값, 나머지는 지금
        값으로 평균한다(`exit_average` 와 같다). 실현 잔고만 재면 열린 매매가 깊이 물린 날이 낙폭에
        안 잡혀 효율이 부풀었다(T968 §11 ③ — 재현 MDD 가 자정 실현 잔고라 평가 MDD 보다 얕을 수
        있음).
    """
    if record.margin_used is None or record.entry <= 0 or mark <= 0:
        return Decimal(0)
    sign = record.direction.sign
    if record.half_at is None:
        average = mark
    else:
        taken = record.planned_first if record.half_price is None else record.half_price
        average = (taken + mark) / Decimal(2)
    base = record.margin_used
    exposure = (
        record.filled_leverage
        if record.filled_leverage is not None
        else record.filled_ratio * record.leverage
    )
    pnl = base * exposure * (average - record.entry) * sign / record.entry
    if record.add_exposure > 0 and record.add_price is not None and record.add_price > 0:
        pnl += base * record.add_exposure * (mark - record.add_price) * sign / record.add_price
    return pnl + record.realized_adjust


class ReplayInputError(RuntimeError):
    """재현에 판이 선언한 진입 문의 입력이 빠졌다 — 걷기 전에 멈춘다(T968 · 규칙 #8)."""


_FRAME_MINUTES = {Timeframe.M15: 15, Timeframe.H1: 60, Timeframe.H4: 240}


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
    def minutes(self) -> int:
        """걸음 간격(분) — 세션의 걸음 축. 15분은 T345 조기 진입 재현(2026-10-02)."""
        return _FRAME_MINUTES[self.session.step_frame]

    @property
    def hours(self) -> int:
        """걸음 간격(시간 · 15분 걸음은 1) — 예전 호출 자리용."""
        return max(1, self.minutes // 60)


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
        mtm: `(UTC 자정, 평가 잔고)` — 실현 잔고 + 열린 매매 평가 손익(코인 판 · 엔진 다리 ·
            비용 · 펀딩 전 · T971). 연구 엔진의 평가 MDD 와 같은 자로 재려고 둔다.
    """

    equity: list[tuple[datetime, Decimal]] = field(default_factory=list[tuple[datetime, Decimal]])
    events: list[ReplayEvent] = field(default_factory=list[ReplayEvent])
    mtm: list[tuple[datetime, Decimal]] = field(default_factory=list[tuple[datetime, Decimal]])


def assemble(
    boards: Sequence[ReplayBoard],
    legs: Sequence[FundLeg],
    capital: Decimal,
    slots: int,
    engines: Sequence[ReplayEngineLeg] = (),
) -> Coordinator:
    """판들을 한 펀드로 묶는다 — 실계좌 펀드 생성과 같은 순서.

    `_create_fund` · `_wire_gate` · 첫 틱 순서를 따른다.

    Args:
        boards: 판들(세션 원장은 실계좌처럼 지갑 · 몫 · 예산이 들어 있어야 한다).
        legs: 다리 선언(`declared_legs`).
        capital: 시작 총자본.
        slots: 자리 수(예산 = 총자본 ÷ 자리).
        engines: 엔진 다리들(T608) — 실계좌처럼 조정자 포트로 끼운다(실현이 총자본에 든다 ·
            브레이크에서 뺀 다리면 `isolate` 가 귀속 키로 가린다 · 다리 문은 세션 포트만 받는다).

    Returns:
        조정자 — 첫 틱으로 예산을 나눠 준 뒤다. 판은 `fund_ready` 로 풀려 있다.
    """
    names = [(b.symbol, Decimal(1)) for b in boards] + [(e.symbol, Decimal(1)) for e in engines]
    basket = Basket(members=as_members(names))
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=capital), slots=slots)
    ports: dict[str, Any] = {b.symbol: SessionBridge(session=b.session) for b in boards}
    for leg in engines:
        ports[leg.symbol] = leg.engine
    coordinator = Coordinator(engine=engine, ports=dict(ports))
    # ⭐ 420차 — 실계좌 `_wire_gate` 와 같다: 브레이크에서 뺀 다리가 있으면
    #    본 다리 원장 낙폭을 준다.
    coordinator.isolate(isolated_attributions(legs))
    wire_legs(coordinator.ports, legs, coordinator.brake_drawdown)
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
        engines: Sequence[ReplayEngineLeg] = (),
        membership: Callable[[str, datetime], frozenset[str] | None] | None = None,
        market_values: Callable[[date, WashedRule], tuple[Decimal | None, Decimal | None] | None]
        | None = None,
        release_schedule: ReleaseSchedule | None = None,
        allow_unknown_inputs: bool = False,
    ) -> None:
        """걸음을 준비한다.

        Args:
            boards: 판들(`assemble` 뒤).
            coordinator: 펀드 조정자.
            ref_4h: 기준(BTC) 4H 봉 오름차순 — 국면값을 여기서 낸다.
            seed: 같은 시각 판 순서를 섞는 씨앗(실계좌는 도착 순서).
            engines: 엔진 다리들(T608) — 정시마다 봉을 흘리고 마감을 처리한다 · 증거금을 나눠 쓴다.
            membership: `(종목, 시각) → 신규 진입을 허용할 다리 id`(T786 · 분기 우주 펀드) —
                실계좌 `_universe_step` 이 세션에 거는 `entry_legs` 와 같다. 빈 집합이고 보유가
                없으면 그 걸음은 커서만 민다(`Session.idle_advance` · 실계좌는 그 판을 내린다).
                None 이면 예전과 같다(고정 목록).
            market_values: `(날, 선언) → (종목 폭, 뒤처진 묶음 수익)` — 그날 종가까지로 잰 시장
                전체 값. 실계좌는 러너가 DB 줄을 읽어 넣는다(`_inject_market_values`). None 이면
                값을 모르는 것으로 돈다(`entry_washed` 를 선언한 다리는 진입을 보류한다).
            release_schedule: 발표 일정(`entry_event_window`) — 실계좌는 러너가 설정 파일에서
                읽어 넣는다. None 이면 모르는 것으로 돈다(그 문을 선언한 다리는 진입 보류).
            allow_unknown_inputs: 참이면 선언한 문의 입력(일정 · 시장 값)이 없어도 돈다(옛 재현과
                맞대기용). 거짓(기본)이면 시작 점검(`check_inputs`)이 막는다.

        Raises:
            ReplayInputError: 판의 매매법이 선언한 문의 입력이 없을 때(`allow_unknown_inputs` 거짓).
        """
        self.boards = list(boards)
        self.coordinator = coordinator
        self.engines = list(engines)
        self._loop: asyncio.AbstractEventLoop | None = asyncio.new_event_loop() if engines else None
        self._ref = list(ref_4h)
        self._ref_ends = [c.ts + FOUR for c in self._ref]
        self._rnd = random.Random(seed)
        self._membership = membership
        self._market_values = market_values
        self._release_schedule = release_schedule
        self._allow_unknown = allow_unknown_inputs
        for board in self.boards:
            board.session.release_schedule = release_schedule
        self.result = ReplayResult()
        if not allow_unknown_inputs:
            self.check_inputs()

    def check_inputs(self, start: datetime | None = None, end: datetime | None = None) -> None:
        """판들이 선언한 진입 문의 **입력이 다 들어왔나** 걷기 전에 본다 (T968).

        실계좌 러너는 일정 · 시장 전체 값 · 기준 봉을 넣는데 재현이 빠뜨리면 세션은 "모름 = 보류"
        (규칙 #8-1)로 그 다리 진입을 조용히 막고, 결과는 그럴듯한 숫자로 나온다(T968 — 숏 다리
        0 · 열 달 거의 무매매). 그래서 재현에서는 모름을 결과로 두지 않고 시작 전에 멈춘다.

        Args:
            start: 걷기 시작(주면 기준 봉 · 일정이 그 시각을 덮는지도 본다).
            end: 걷기 끝(주면 일정 끝도 본다).

        Raises:
            ReplayInputError: 빠진 입력 · 모자란 기준 봉 · 창을 못 덮는 일정.
        """
        books = [b for board in self.boards for b in board.session.playbooks]
        missing: list[str] = []
        windows = [b.entry_event_window for b in books if b.entry_event_window is not None]
        if windows and self._release_schedule is None:
            missing.append("발표 일정(entry_event_window)")
        if windows and self._release_schedule is not None and start is not None and end is not None:
            span = max(timedelta(hours=float(w.hours)) for w in windows)
            if (
                start - span < self._release_schedule.since
                or end + span > self._release_schedule.horizon
            ):
                missing.append(
                    f"발표 일정이 창을 못 덮음({self._release_schedule.since:%Y-%m-%d} ~ "
                    f"{self._release_schedule.horizon:%Y-%m-%d})"
                )
        if any(b.entry_washed is not None for b in books) and self._market_values is None:
            missing.append("시장 전체 값(entry_washed)")
        if start is not None:
            have = bisect_right(self._ref_ends, start)
            need = max(
                (
                    board.needs.bars
                    for board in self.boards
                    if board.needs and not board.needs.empty
                ),
                default=0,
            )
            if need and have <= need:
                missing.append(f"기준 4H 봉 {have} 개 ≤ 국면값 요구 {need} 개(시작 전 마감 봉)")
        if missing:
            raise ReplayInputError("재현 입력 빠짐 — " + " · ".join(missing))

    def run(self, start: datetime, end: datetime) -> ReplayResult:
        """`start` 다음 시간부터 `end` 까지 한 시간씩 민다.

        Args:
            start: 판들의 봉인 시작(세션 커서)과 같은 시각(UTC · 정시).
            end: 끝 시각.

        Returns:
            걸은 결과.
        """
        # ⭐ 시계 눈금 = 판들 걸음 중 가장 잔 것(최대 1시간). 전부 1H · 4H 면 예전과 한 시간씩 ·
        #    같은 순서 · 같은 난수 소비라 결과가 한 비트도 안 바뀐다. 15분 판(T345 조기 진입)이
        #    있으면 15분씩 밀되 정시 일(기준 국면 · 펀딩 · 펀드 틱 · 자정 잔고)은 정시에만 한다.
        if not self._allow_unknown:
            self.check_inputs(start, end)
        tick = min([HOUR, *(timedelta(minutes=b.minutes) for b in self.boards)])
        t = start
        while t < end:
            t += tick
            on_hour = t.minute == 0
            if on_hour and t.hour % 4 == 0:
                self._inject_ref(t)
                self._inject_market(t)
            if on_hour:
                self._inject_funding(t)
            if on_hour and self.engines:
                self._advance_engines(t)
            of_day = t.hour * 60 + t.minute
            due = [b for b in self.boards if of_day % b.minutes == 0]
            self._rnd.shuffle(due)
            for board in due:
                if self._membership is not None:
                    allowed = self._membership(board.symbol, t)
                    if board.session.entry_legs != allowed:
                        board.session.entry_legs = allowed
                    if board.session.idle_advance():
                        continue
                self._halt_if_exhausted(board, t)
                board.session.step()
                self._after_step(board, t)
            if on_hour and t.hour % 4 == 0:
                self.coordinator.tick()
            if on_hour and t.hour == 0:
                balance = self.coordinator.engine.balance
                self.result.equity.append((t, balance))
                self.result.mtm.append((t, balance + self.open_value()))
        return self.result

    def open_value(self) -> Decimal:
        """지금 열린 매매 전부의 평가 손익 — 코인 판(마지막 닫힌 봉 종가) + 엔진 다리 몫 (T971).

        Returns:
            USDT 합(비용 · 펀딩 전). 보유가 없으면 0.
        """
        total = Decimal(0)
        for board in self.boards:
            held = board.session.positions
            if not held:
                continue
            bars = board.session.feed.judged(board.session.step_frame)
            if not bars:
                continue
            mark = Decimal(str(bars[-1].close))
            total += sum((marked_pnl(record, mark) for record in held), Decimal(0))
        for leg in self.engines:
            total += leg.unrealized()
        return total

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
        # 같은 필요(`RefNeeds` · 값 동등)는 한 번만 계산한다 — 판이 많은 동적 재현(T786)에서
        # 같은 식을 판마다 되풀이하던 몫. 결과는 같다(순수 함수 · 같은 입력).
        memo: dict[RefNeeds, Any] = {}
        for board in self.boards:
            needs = board.needs
            if needs is None or needs.empty:
                continue
            s = board.session
            try:
                if needs not in memo:
                    try:
                        memo[needs] = reference_regime(closed, needs)
                    except ValueError as exc:
                        memo[needs] = exc
                got = memo[needs]
                if isinstance(got, ValueError):
                    raise got
            except ValueError:
                clear_ref_regime(s)  # T968 — 실계좌 러너와 같은 한 길(ref_inputs)
                continue
            apply_ref_regime(s, needs, got)

    def _inject_market(self, t: datetime) -> None:
        """4H 경계마다 시장 전체 값(전날 종가까지)을 넣는다 — `entry_washed` 를 선언한 판만.

        실계좌 `_inject_market_values` 와 같은 날 셈이다: 값의 날 = `t` 기준 마지막으로 닫힌 UTC 날.
        """
        day = market_day_of(t)
        for board in self.boards:
            s = board.session
            rule = washed_rule_of(s.playbooks)
            if rule is None:
                continue
            got = None if self._market_values is None else self._market_values(day, rule)
            s.market_breadth, s.market_lag_ret = (None, None) if got is None else got

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
        for leg in self.engines:
            held += leg.held_margin()
        return self.coordinator.engine.balance - held

    def wait(self, coro: Any) -> Any:
        """엔진 코루틴을 되감기 루프에서 끝까지 돈다(엔진 다리가 있을 때만 루프가 있다)."""
        if self._loop is None:
            raise RuntimeError("엔진 다리가 없는 되감기에는 루프가 없다")
        return self._loop.run_until_complete(coro)

    def _advance_engines(self, t: datetime) -> None:
        """정시 — 엔진마다 끝난 1H 봉을 모의 거래소에 흘리고, 처리 시각이 지난 거래일을 마감한다."""
        for leg in self.engines:
            for kind, detail in self.wait(leg.advance(t)):
                self.result.events.append(ReplayEvent(t, leg.symbol, kind, "", detail))

    def _ask_yield(self, board: ReplayBoard, rec: Any, t: datetime) -> None:
        """러너 `_ask_yield`(T533)와 같다.

        가용이 max(예산, 이 진입 증거금) 보다 작으면 엔진 다리에 비켜 달라고 묻고, 엔진은 늦게 든
        몫부터 닫는다. 엔진이 없으면 아무것도 안 한다.
        """
        if not self.engines:
            return
        led = board.session.ledger
        budget = led.sizing_base
        lev = led.leverage
        want = max(budget, budget * rec.leverage / lev) if lev > 0 else budget
        spare = self.spare()
        if spare >= want:
            return
        freed = Decimal(0)
        for leg in self.engines:
            freed += self.wait(leg.engine.yield_margin(want))
        self._note(
            t, board, "yield", rec.trade_id, f"want {want:.2f} spare {spare:.2f} freed {freed:.2f}"
        )

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
                self._ask_yield(board, rec, t)
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
                        # 실계좌 `_skip_unfillable` 과 같다 — 몫 모드면 그 몫만 놓는다.
                        #   통째로 놓으면 몫이 여럿일 때 T320 오류로 멈췄다(2026-10-03).
                        if any(
                            getattr(b, "share_same_side", False) for b in board.session.playbooks
                        ):
                            board.session.release(leg=rec.playbook)
                        else:
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
                if why == "margin" and self.engines:
                    # 실계좌 `_yield_for_add` 와 같다 — 버리기 전에 엔진 다리에 먼저 묻는다.
                    want = add_wanted_spare(
                        rec,
                        sizing_base=led.sizing_base,
                        spec=board.spec,
                        ledger_leverage=led.leverage,
                    )
                    spare = self.spare()
                    freed = Decimal(0)
                    for leg in self.engines:
                        freed += self.wait(leg.engine.yield_margin(want))
                    self._note(
                        t,
                        board,
                        "yield",
                        tid,
                        f"add want {want:.2f} spare {spare:.2f} freed {freed:.2f}",
                    )
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
