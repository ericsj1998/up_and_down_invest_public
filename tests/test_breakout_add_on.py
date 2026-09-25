"""T308 — 돌파 롱 불타기 판정: 선언 · 파서 · 세션 기록.

세션은 **판정만** 한다 — 진입 뒤 1H 종가가 진입가 아래로 한 번도 안 닫힌 채 문턱 이상에서 닫히면
그 봉 종가 · 마감 시각을 기록에 적는다. 선언이 없으면 한 비트도 안 달라진다(§5.6.2).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks
from updown.analysis.playbook.types import AddOn, Family, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.decision.portfolio_rules import Grant
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
ENTRY_AT = START + timedelta(hours=20)
ENTRY = Decimal(530)
RULE = AddOn(confirm_pct=Decimal("0.105"), frac=Decimal("0.5"))
RESEARCH = "private_strategy"
MACD_RESEARCH = "private_strategy"


def _book(add_on: AddOn | None) -> Playbook:
    """탐지는 안 도는 1H 플레이북 — 캔들 색을 안 보고 들고 간다(돌파 롱 다리와 같다)."""
    return Playbook(
        playbook_id="add",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
        hold_through_turn=True,
        full_ride=True,
        add_on=add_on,
    )


def _session(
    book: Playbook,
    closes: dict[int, int],
    *,
    opened: datetime = ENTRY_AT,
    entry: Decimal = ENTRY,
    direction: Direction = Direction.LONG,
) -> Session:
    """시각(시간 번호)별 1H 종가를 주는 30시간 봉 · 20~28시 봉인 — 한 시간 안의 5m 는 평평하다."""

    def close_of(hour: int) -> Decimal:
        return Decimal(closes[max(k for k in closes if k <= max(hour, 0))])

    def candle(frame: Timeframe, ts: datetime) -> Candle:
        hour = int((ts - START).total_seconds() // 3600)
        open_, close = close_of(hour - 1), close_of(hour)
        return Candle(
            instrument=BTC,
            timeframe=frame,
            ts=ts,
            open=open_,
            high=max(open_, close) + 1,
            low=min(open_, close) - 1,
            close=close,
            volume=Decimal(10),
        )

    hours = 30
    source = {
        Timeframe.M5: [
            candle(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(hours * 12)
        ],
        Timeframe.H1: [candle(Timeframe.H1, START + timedelta(hours=i)) for i in range(hours)],
    }
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, Seal(start=ENTRY_AT, end=START + timedelta(hours=28))),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    record = TradeRecord(
        trade_id="t-add",
        playbook=book.attribution,
        actor=Actor.SYSTEM,
        direction=direction,
        placed_at=opened,
        opened_at=opened,
        entry=entry,
        # 손절 · 목표는 닿지 않을 만큼 멀다 — 방향에 따라 반대쪽
        planned_stop=Decimal(400) if direction is Direction.LONG else Decimal(700),
        planned_target=Decimal(5_000) if direction is Direction.LONG else Decimal(1),
        planned_first=Decimal(5_000) if direction is Direction.LONG else Decimal(1),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return session


def _walk(session: Session) -> TradeRecord:
    """끝까지 걸어가고 그 한 매매의 마지막 기록을 돌려준다."""
    for _ in range(10_000):
        if session.finished:
            break
        session.step()
    (record,) = session.ledger.records
    return record


class TestDeclaration:
    def test_rule_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            AddOn(confirm_pct=Decimal(0), frac=Decimal("0.5"))
        with pytest.raises(ValueError):
            AddOn(confirm_pct=Decimal("0.1"), frac=Decimal(0))
        with pytest.raises(ValueError):
            AddOn(confirm_pct=Decimal("0.1"), frac=Decimal("1.5"))

    def test_parser_names_the_playbook(self) -> None:
        with pytest.raises(PlaybookConfigError, match=r"x\.add_on"):
            select._add_on({"confirm_pct": "0.1"}, "x")  # pyright: ignore[reportPrivateUsage]

    def test_only_the_research_book_carries_it(self) -> None:
        books = {item.playbook_id: item for item in load_playbooks()}
        assert books[RESEARCH].add_on == RULE
        assert not books[RESEARCH].listed
        macd_rule = AddOn(confirm_pct=Decimal("0.06"), frac=Decimal("0.5"))
        assert books[MACD_RESEARCH].add_on == macd_rule
        assert not books[MACD_RESEARCH].listed
        on = {name for name, book in books.items() if book.add_on is not None}
        assert on == {RESEARCH, MACD_RESEARCH, "private_strategy", "private_strategy"}
        # 🔴 실계좌 다리는 측정한 값 그대로 · 선언 버전은 그대로(펀드 다리 귀속 키가 버전을 품는다)
        assert books["private_strategy"].add_on == RULE
        assert books["private_strategy"].add_on == macd_rule
        assert books["private_strategy"].version == "0.1.0"
        assert books["private_strategy"].version == "0.1.0"

    @pytest.mark.parametrize(
        ("research", "origin"),
        [(RESEARCH, "private_strategy"), (MACD_RESEARCH, "private_strategy")],
    )
    def test_research_book_is_the_origin_with_one_switch(self, research: str, origin: str) -> None:
        books = {item.playbook_id: item for item in load_playbooks()}
        loose = ("playbook_id", "add_on", "listed", "label", "backtest_note")
        for spec in dataclasses.fields(Playbook):
            if spec.name in loose:
                continue
            got = getattr(books[research], spec.name)
            assert got == getattr(books[origin], spec.name), spec.name


class TestSession:
    def test_confirmed_close_is_the_add(self) -> None:
        # 진입 뒤 1H 종가 540 → 560 → 590(≥ 530 x 1.105 = 585.65) — 그 봉 종가 · 마감 시각에 한 번
        got = _walk(_session(_book(RULE), {0: 530, 20: 540, 21: 560, 22: 590, 23: 620}))
        assert got.outcome is Outcome.OPEN
        assert got.add_at == START + timedelta(hours=23)
        assert got.add_price == Decimal(590)
        assert got.add_frac == Decimal("0.5") and not got.add_broken

    def test_a_close_below_entry_first_kills_it(self) -> None:
        got = _walk(_session(_book(RULE), {0: 530, 20: 529, 21: 600}))
        assert got.add_at is None and got.add_price is None and got.add_broken

    def test_the_entry_bar_itself_is_not_after_entry(self) -> None:
        # 진입 봉(19시 · 종가 = 진입가 근처)이 문턱 위여도 진입 뒤가 아니다 — 20시 봉부터 센다
        got = _walk(_session(_book(RULE), {0: 530, 19: 600, 20: 540}))
        assert got.add_at is None and not got.add_broken

    def test_a_fill_above_the_breakout_close_is_not_a_pullback(self) -> None:
        # 실계좌: 돌파봉(19시) 종가 530 에 신호 · 19:55 5m 봉에서 531 에 체결.
        # 체결 봉은 20:00 에 닫힌다 —
        # 돌파봉 종가(530 < 531)는 체결 **전** 마감이라 되돌림이 아니다(371차).
        got = _walk(
            _session(
                _book(RULE),
                {0: 530, 20: 540, 21: 560, 22: 590},
                opened=START + timedelta(hours=19, minutes=55),
                entry=Decimal(531),
            )
        )
        assert not got.add_broken
        assert got.add_at == START + timedelta(hours=23) and got.add_price == Decimal(590)

    def test_frozen_book_writes_nothing(self) -> None:
        got = _walk(_session(_book(None), {0: 530, 20: 540, 21: 560, 22: 590, 23: 620}))
        assert got.add_at is None and got.add_price is None
        assert got.add_frac == Decimal(0) and not got.add_broken


CONFIRMED = {0: 530, 20: 540, 21: 560, 22: 590, 23: 620}


class _HalvingGate:
    """불타기를 반으로 줄이는 문 — 받은 물음을 적어 둔다."""

    def __init__(self) -> None:
        self.asked: list[tuple[datetime, Decimal]] = []

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        _ = at
        return Grant(exposure)

    def grant_add(self, at: datetime, exposure: Decimal) -> Grant:
        self.asked.append((at, exposure))
        return Grant(exposure / 2, None, "notional")


class _LegGate:
    """다리별 문 — 어느 다리로 물었는지 적는다."""

    def __init__(self) -> None:
        self.legs: list[str] = []

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        _ = (at, exposure)
        return Grant(Decimal(0), "leg")

    def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        _ = (leg, at)
        return Grant(exposure)

    def grant_add_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        _ = (at, exposure)
        self.legs.append(leg)
        return Grant(Decimal(0), "notional")


class _OldGate:
    """불타기를 모르는 문."""

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        _ = at
        return Grant(exposure)


class TestSessionAsksTheFund:
    def test_no_gate_takes_the_request(self) -> None:
        # 단독 세션(백테스트 · RUN) — 처음 노출(기본 1) x 0.5 를 그대로 적는다.
        got = _walk(_session(_book(RULE), CONFIRMED))
        assert got.add_exposure == Decimal("0.5") and got.add_held is None

    def test_the_gate_sizes_the_add(self) -> None:
        session = _session(_book(RULE), CONFIRMED)
        gate = _HalvingGate()
        session.entry_gate = gate
        got = _walk(session)
        # 물음은 한 번 · 처음 노출 x 비율 · 시각은 진입과 같은 자(걸음 봉 시작) — 22:55 5m 봉이
        # 닫히며 22시 1H 봉이 마감된다. 기록의 추가 시각은 확인 봉 마감(23:00)이다.
        assert gate.asked == [(START + timedelta(hours=22, minutes=55), Decimal("0.5"))]
        assert got.add_at == START + timedelta(hours=23)
        assert got.add_exposure == Decimal("0.25") and got.add_held is None
        assert session.funnel.get("add:fit:notional") == 1

    def test_a_leg_gate_is_asked_by_the_leg(self) -> None:
        session = _session(_book(RULE), CONFIRMED)
        gate = _LegGate()
        session.entry_gate = gate
        got = _walk(session)
        assert gate.legs == [got.playbook]
        # 막혀도 판정(시각 · 가격)은 남는다 — 무엇을 놓쳤는지 되물을 수 있어야 한다.
        assert got.add_at is not None and got.add_exposure == 0 and got.add_held == "notional"

    def test_a_gate_that_does_not_know_adds_holds_them(self) -> None:
        session = _session(_book(RULE), CONFIRMED)
        session.entry_gate = _OldGate()
        got = _walk(session)
        assert got.add_exposure == 0 and got.add_held == "no_add_gate"


class TestShortMirror:
    """숏 거울(374 · 376차) — 진입가 위 마감 없이 진입가 x (1 - 문턱) 이하로 닫히면 추가."""

    def test_confirmed_drop_is_the_add(self) -> None:
        # 530 → 520 → 500 → 470(≤ 530 x 0.895 = 474.35) — 그 봉 종가 · 마감 시각
        got = _walk(
            _session(
                _book(RULE), {0: 530, 20: 520, 21: 500, 22: 470, 23: 450}, direction=Direction.SHORT
            )
        )
        assert got.outcome is Outcome.OPEN
        assert got.add_at == START + timedelta(hours=23) and got.add_price == Decimal(470)
        assert not got.add_broken and got.add_exposure == Decimal("0.5")

    def test_a_close_above_entry_first_kills_it(self) -> None:
        got = _walk(_session(_book(RULE), {0: 530, 20: 531, 21: 450}, direction=Direction.SHORT))
        assert got.add_at is None and got.add_broken

    def test_a_rise_is_not_a_short_confirmation(self) -> None:
        # 롱이면 확인이었을 상승 — 숏에는 진입가 위 마감(되돌림)일 뿐이다
        got = _walk(_session(_book(RULE), CONFIRMED, direction=Direction.SHORT))
        assert got.add_at is None and got.add_broken
