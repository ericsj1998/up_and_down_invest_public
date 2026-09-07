"""취소 직전에 채워진 지정가를 원장으로 되살린다 (2026-08-23 사고 · 원장 없음/거래소 보유중).

세션이 대기 만료로 `_waiting` 을 비운 뒤 거래소 체결이 확인되면, 만료된 대기 기록의 계획 그대로
(손절·목표) 진입가·시각만 사실로 바꿔 보유로 올린다. 막아야 하는 실패:
1. 🔴 체결을 버려서 아무도 관리하지 않는 포지션이 남는 것 (조건부 손절도 안 걸린다)
2. 🔴 다른 매매 id 의 체결을 엉뚱한 계획에 붙이는 것
3. 🔴 이미 보유 중인데 또 올리는 것 (포지션 1개)
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
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)


def _candle(frame: Timeframe, ts: datetime) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=Decimal(525),
        high=Decimal(538),
        low=Decimal(522),
        close=Decimal(535),
        volume=Decimal(10),
    )


def _session() -> Session:
    minutes = 60 * 26
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(minutes // 5)
        ],
        Timeframe.M15: [
            _candle(Timeframe.M15, START + timedelta(minutes=15 * i)) for i in range(minutes // 15)
        ],
    }
    book = Playbook(
        playbook_id="box",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
    )
    seal = Seal(start=START + timedelta(hours=20), end=START + timedelta(hours=24))
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    for _ in range(3):
        session.step()
    return session


def _expired(trade_id: str = "t-late") -> TradeRecord:
    """만료로 거둔 대기 기록 — 계획(손절·목표)은 그대로 들고 있다."""
    return TradeRecord(
        trade_id=trade_id,
        playbook="box@0",
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=START + timedelta(hours=20),
        opened_at=None,
        entry=Decimal(524),
        planned_stop=Decimal(510),
        planned_target=Decimal(560),
        planned_first=Decimal(542),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )


def test_late_fill_is_adopted_with_the_expired_plan() -> None:
    session = _session()
    session._last_expired = _expired()  # pyright: ignore[reportPrivateUsage]
    made = session.adopt_late_fill("t-late", Decimal(523), Decimal(1))
    assert made is not None
    assert made.entry == Decimal(523), "진입가는 거래소가 말한 체결가"
    assert made.planned_stop == Decimal(510), "손절은 만료된 계획 그대로"
    assert made.opened_at is not None
    assert session._open is made  # pyright: ignore[reportPrivateUsage]
    assert made in session.ledger.records
    assert session.funnel.get("entered:box@0") == 1


def test_other_trade_id_is_refused() -> None:
    session = _session()
    session._last_expired = _expired("t-other")  # pyright: ignore[reportPrivateUsage]
    assert session.adopt_late_fill("t-late", Decimal(523), Decimal(1)) is None
    assert session._open is None  # pyright: ignore[reportPrivateUsage]


def test_refused_while_holding() -> None:
    session = _session()
    session._last_expired = _expired()  # pyright: ignore[reportPrivateUsage]
    session.adopt_late_fill("t-late", Decimal(523), Decimal(1))
    session._last_expired = _expired("t-late")  # pyright: ignore[reportPrivateUsage]
    assert session.adopt_late_fill("t-late", Decimal(523), Decimal(1)) is None, "포지션 1개"


def test_nothing_expired_nothing_adopted() -> None:
    session = _session()
    assert session.adopt_late_fill("t-late", Decimal(523), Decimal(1)) is None
