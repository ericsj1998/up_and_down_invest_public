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
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
ENTRY_AT = START + timedelta(hours=20)
ENTRY = Decimal(530)
RULE = AddOn(confirm_pct=Decimal("0.105"), frac=Decimal("0.5"))
RESEARCH = "private_strategy"


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
        direction=Direction.LONG,
        placed_at=opened,
        opened_at=opened,
        entry=entry,
        planned_stop=Decimal(400),
        planned_target=Decimal(5_000),
        planned_first=Decimal(5_000),
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
        assert {name for name, book in books.items() if book.add_on is not None} == {RESEARCH}

    def test_research_book_is_a_plus_with_one_switch(self) -> None:
        books = {item.playbook_id: item for item in load_playbooks()}
        loose = ("playbook_id", "add_on", "listed", "label", "backtest_note")
        for spec in dataclasses.fields(Playbook):
            if spec.name in loose:
                continue
            got = getattr(books[RESEARCH], spec.name)
            assert got == getattr(books["private_strategy"], spec.name), spec.name


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
