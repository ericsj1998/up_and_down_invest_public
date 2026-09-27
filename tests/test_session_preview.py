"""마감 전 예비 신호 — `Session.preview` 는 형성 중 봉으로만 묻고 판정을 안 건드린다 (2026-09-27).

🔴 못 박는 것:
  - 형성 중 봉을 **마지막 마감 봉 바로 다음**에 붙여 같은 `propose` 에 넣는다
    (틈이 있으면 묻지 않는다)
  - 판정 캐시(`_cache` · `_shot`) · 원장이 그대로다 — 미마감 봉이 판정에 새지 않는다(절대 규칙 #5)
  - 1h 마감을 물을 때 4h 는 닫힌 봉 그대로(상위 TF 문은 닫힌 봉만)
  - 막힌 후보 · 진입 보류(`entry_hold`) · 보유 중인 판은 예비 신호가 아니다
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.analysis.detectors.base import MarketContext
from updown.analysis.playbook.types import Family, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.orchestration.playbook_run import Proposal
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward import session as session_mod
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
SPIKE = Decimal(2000)


def _book(**kw: Any) -> Playbook:
    base = Playbook(
        playbook_id="pv",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
    )
    return dataclasses.replace(base, **kw)


def _candle(frame: Timeframe, ts: datetime, close: Decimal = Decimal(1000)) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=Decimal(1000),
        high=max(close, Decimal(1000)) + 1,
        low=min(close, Decimal(1000)) - 1,
        close=close,
        volume=Decimal(10),
    )


def _session(book: Playbook | None = None) -> Session:
    hours = 48
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(hours * 12)
        ],
        Timeframe.H1: [_candle(Timeframe.H1, START + timedelta(hours=i)) for i in range(hours)],
        Timeframe.H4: [
            _candle(Timeframe.H4, START + timedelta(hours=4 * i)) for i in range(hours // 4)
        ],
    }
    session = Session(
        instrument=BTC,
        playbooks=(book or _book(),),
        feed=SealedFeed(
            source, Seal(start=START + timedelta(hours=30), end=START + timedelta(hours=46))
        ),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    session.step()
    return session


def _setup(short: bool = False) -> TradeSetup:
    entry = SPIKE
    stop = entry + 50 if short else entry - 50
    return TradeSetup(
        setup_type="spike",
        rule_version="spike@0",
        entry_trigger=EntryTrigger.TOUCH,
        entry_plan=(EntryLeg(price=entry, ratio=Decimal(1)),),
        avg_entry=entry,
        stop_loss=stop,
        tp_ladder=(
            TakeProfitStep(
                price=entry - 100 if short else entry + 100, ratio=Decimal(1), then=None
            ),
        ),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
        rr_ratio=Decimal(2),
        confidence=0.5,
        evidence=(),
    )


class Spy:
    """`propose` 대역 — 진입 축 마지막 봉이 SPIKE 이상이면 후보 하나."""

    def __init__(self, *, blocked: tuple[str, ...] = (), short: bool = False) -> None:
        self.seen: list[dict[Timeframe, Candle]] = []
        self.blocked = blocked
        self.short = short

    def __call__(self, ctx: MarketContext, **kw: Any) -> list[Proposal]:
        self.seen.append({frame: rows[-1] for frame, rows in ctx.candles.items() if rows})
        frame: Timeframe = kw["timeframe"]
        rows = ctx.candles.get(frame) or []
        books: list[Playbook] = list(kw["playbooks"])
        if rows and rows[-1].close >= SPIKE and books:
            return [Proposal(books[0], _setup(self.short), blocked=self.blocked)]
        return []


def _forming(session: Session, frame: Timeframe, close: Decimal, *, gap: int = 0) -> Candle:
    last = session.feed.judged(frame)[-1]
    span = session_mod.frame_span(frame)
    return _candle(frame, last.ts + span * (1 + gap), close)


def test_forming_bar_fires_and_closed_bars_do_not(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    spy = Spy()
    monkeypatch.setattr(session_mod, "propose", spy)
    assert session.preview({Timeframe.H1: _forming(session, Timeframe.H1, Decimal(1000))}) == ()
    got = session.preview({Timeframe.H1: _forming(session, Timeframe.H1, SPIKE)})
    assert len(got) == 1 and got[0].playbook.playbook_id == "pv"


def test_preview_leaves_judgement_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    before_shot = session.look()
    cache = dict(session._cache)  # pyright: ignore[reportPrivateUsage]
    records = list(session.ledger.records)
    funnel = dict(session.funnel)
    monkeypatch.setattr(session_mod, "propose", Spy())
    session.preview({Timeframe.H1: _forming(session, Timeframe.H1, SPIKE)})
    after = session._cache  # pyright: ignore[reportPrivateUsage]
    assert {f: (s.bars, s.rows[-1].ts) for f, s in after.items()} == {
        f: (s.bars, s.rows[-1].ts) for f, s in cache.items()
    }
    assert session._shot is before_shot  # pyright: ignore[reportPrivateUsage]
    assert list(session.ledger.records) == records
    assert session.funnel == funnel


def test_gap_is_not_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    monkeypatch.setattr(session_mod, "propose", Spy())
    assert session.preview({Timeframe.H1: _forming(session, Timeframe.H1, SPIKE, gap=1)}) == ()


def test_hour_close_keeps_four_hour_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    spy = Spy()
    monkeypatch.setattr(session_mod, "propose", spy)
    closed_h4 = session.feed.judged(Timeframe.H4)[-1]
    forming_h4 = _forming(session, Timeframe.H4, SPIKE)
    session.preview(
        {Timeframe.H1: _forming(session, Timeframe.H1, SPIKE), Timeframe.H4: forming_h4}
    )
    # 1h 플레이북만 있으므로 1h 마감만 묻는다 — 4h 는 넣더라도 닫힌 봉 그대로여야 한다.
    for seen in spy.seen:
        if Timeframe.H4 in seen:
            assert seen[Timeframe.H4].ts == closed_h4.ts


def test_blocked_hold_and_holding_are_not_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    monkeypatch.setattr(session_mod, "propose", Spy(blocked=("막힘",)))
    assert session.preview({Timeframe.H1: _forming(session, Timeframe.H1, SPIKE)}) == ()

    # 진입 보류 — 기준(BTC)이 SMA 아래면 새로 안 산다(_enter 와 같은 규칙)
    held_book = _session(_book(entry_ref_ma_gate=50))
    held_book.ref_above = False
    monkeypatch.setattr(session_mod, "propose", Spy())
    bar = _forming(held_book, Timeframe.H1, SPIKE)
    assert held_book.entry_hold(held_book.playbooks[0], Direction.LONG) == "ref_gate"
    assert held_book.preview({Timeframe.H1: bar}) == ()

    # 보유 중 — 새 진입이 없으니 예비 신호도 없다
    session = _session()
    record = TradeRecord(
        trade_id="t",
        playbook="pv@0",
        actor=Actor.SYSTEM,
        placed_at=START,
        entry=Decimal(1000),
        planned_stop=Decimal(900),
        outcome=Outcome.OPEN,
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    assert session.preview({Timeframe.H1: _forming(session, Timeframe.H1, SPIKE)}) == ()


def test_spot_market_drops_short(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    session.short_allowed = False
    monkeypatch.setattr(session_mod, "propose", Spy(short=True))
    assert session.preview({Timeframe.H1: _forming(session, Timeframe.H1, SPIKE)}) == ()
