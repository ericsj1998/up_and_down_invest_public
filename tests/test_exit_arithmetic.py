"""T50 — 청산 산수: 손절 가격 체결(B) · 박스 전환 익절 끄기(C) · 깔때기(③).

둘 다 "켜지 않으면 한 비트도 안 달라진다"가 절반이다 — 동결 버전을 같은 봉에서 나란히
돌려 그것을 잠근다 (§5.6.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    HalfBy,
    Outcome,
    TradeRecord,
)

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
STOP = Decimal(487)
TOUCH_AT = START + timedelta(hours=22)
"""이 15분 동안만 꼬리가 손절선 아래(485)를 찍는다 — 몸통은 여전히 위다."""
TURN_AT = START + timedelta(hours=22)
"""이 시각부터 15m 음봉 셋 — 전환 신호. 진입가 위라 이익 중이고, 손절·목표에는 안 닿는다."""


def _candle(frame: Timeframe, ts: datetime, *, shape: str) -> Candle:
    """`touch`: 꼬리만 손절 아래 · `turn`: 음봉 셋 · 그 밖에는 레벨 위 양봉."""
    open_, close, low = Decimal(525), Decimal(535), Decimal(522)
    if shape == "touch" and TOUCH_AT <= ts < TOUCH_AT + timedelta(minutes=15):
        low = Decimal(485)
    if shape == "turn" and TURN_AT <= ts < TURN_AT + timedelta(minutes=45):
        open_, close, low = Decimal(551), Decimal(545), Decimal(543)
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=open_,
        high=max(open_, close) + 3,
        low=low,
        close=close,
        volume=Decimal(10),
    )


def _book(name: str, *, hold_through_turn: bool = False) -> Playbook:
    """탐지는 안 도는(국면 없음) 플레이북 — 청산 플래그만 다르다."""
    return Playbook(
        playbook_id=name,
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
        hold_through_turn=hold_through_turn,
    )


def _session(book: Playbook, *, shape: str, stop_at_price: bool = False) -> Session:
    """26시간 봉 · 20~24시 봉인 — 보유 기록은 시험이 직접 넣는다."""
    minutes = 60 * 26
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, START + timedelta(minutes=5 * i), shape=shape)
            for i in range(minutes // 5)
        ],
        Timeframe.M15: [
            _candle(Timeframe.M15, START + timedelta(minutes=15 * i), shape=shape)
            for i in range(minutes // 15)
        ],
    }
    seal = Seal(start=START + timedelta(hours=20), end=START + timedelta(hours=24))
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    session.stop_at_price = stop_at_price
    record = TradeRecord(
        trade_id="t-exit",
        playbook=book.attribution,
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(530),
        planned_stop=STOP,
        planned_target=Decimal(900),
        planned_first=Decimal(900),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return session


def _run(session: Session) -> list[TradeRecord]:
    for _ in range(10_000):
        if session.finished:
            break
        session.step()
    return [item for item in session.ledger.records if item.closed_at is not None]


class TestStopAtPrice:
    """B — 닿으면 그 가격. 몸통 확인(기본)은 꼬리만으로는 안 나간다."""

    def test_wick_touch_exits_at_the_stop_price(self) -> None:
        session = _session(_book("box"), shape="touch", stop_at_price=True)
        closed = _run(session)
        assert [item.outcome for item in closed] == [Outcome.STOP_LOSS]
        assert closed[0].exit_price == STOP, "확인 봉 종가가 아니라 손절선 가격이어야 한다"
        assert closed[0].closed_at is not None and closed[0].closed_at >= TOUCH_AT

    def test_confirm_mode_ignores_the_wick(self) -> None:
        """⛔ 동결: 몸통 중심이 손절선 위면 꼬리는 손절이 아니다 — 한 건도 안 닫힌다."""
        session = _session(_book("box"), shape="touch", stop_at_price=False)
        assert _run(session) == []


class TestHoldThroughTurn:
    """C — 캔들 색 전환으로는 안 나간다. 동결은 음봉 셋에 **반익**(첫 신호) 부터 간다."""

    def test_frozen_takes_half_on_three_red_candles(self) -> None:
        """동결 경로: 첫 전환 신호 = 절반 익절 + 손절을 본절로. 전량은 다음 신호다."""
        session = _session(_book("box"), shape="turn")
        _run(session)
        held = session._open  # pyright: ignore[reportPrivateUsage]
        assert held is not None and held.half_at is not None
        assert held.half_by is HalfBy.SIGNAL
        assert held.planned_stop == held.entry

    def test_hold_flag_ignores_the_turn(self) -> None:
        session = _session(_book("box_hold", hold_through_turn=True), shape="turn")
        assert _run(session) == [], "전환 신호에도 목표·손절 전엔 들고 있어야 한다"
        held = session._open  # pyright: ignore[reportPrivateUsage]
        assert held is not None and held.half_at is None, "반익도 전환 신호로는 안 한다"
        assert held.planned_stop == STOP


class TestFunnel:
    """③ — 걸은 봉마다 국면 칸이 늘고, 후보가 없으면 후보 칸은 없다."""

    def test_bars_are_counted_by_regime(self) -> None:
        session = _session(_book("box"), shape="plain")
        _run(session)
        bars = {k: v for k, v in session.funnel.items() if k.startswith("bars:")}
        assert bars and sum(bars.values()) > 0
        assert not any(k.startswith("cand:") for k in session.funnel)
