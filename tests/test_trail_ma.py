"""SMA 추적 손절 (T58 B-3) — 오르는 SMA 로 손절이 상향 트레일된다.

막아야 하는 실패:
1. 🔴 trail_ma 가 켜졌는데 손절이 안 오르면 추세필터 청산이 성립 안 한다.
2. 🔴 손절이 현재가를 넘어(즉시 청산) 서면 안 된다 (safe 체크).
3. 🔴 하향은 절대 없어야 한다 (규칙 #3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

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


def _candle(frame: Timeframe, ts: datetime, close: float) -> Candle:
    v = Decimal(str(close))
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=v,
        high=v + 1,
        low=v - 1,
        close=v,
        volume=Decimal(10),
    )


def _session() -> Session:
    minutes = 60 * 26
    # 꾸준히 오르는 창 — SMA 가 계속 상승한다.
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, START + timedelta(minutes=5 * i), 100 + i * 0.3)
            for i in range(minutes // 5)
        ],
        Timeframe.M15: [
            _candle(Timeframe.M15, START + timedelta(minutes=15 * i), 100 + i * 1.0)
            for i in range(minutes // 15)
        ],
    }
    book = Playbook(
        playbook_id="ma",
        version="0.3.0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
        trail_ma=3,  # 짧은 SMA 로 테스트
    )
    seal = Seal(start=START + timedelta(hours=20), end=START + timedelta(hours=24))
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    record = TradeRecord(
        trade_id="t-long",
        playbook="ma@0.3.0",
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(150),
        planned_stop=Decimal(95),  # 낮게 시작 — SMA 가 이 위로 올라오면 트레일돼야
        planned_target=Decimal(100_000),  # 먼 목표 — 익절로 안 닫히게
        planned_first=Decimal(100_000),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return session


def test_stop_trails_up_to_rising_sma() -> None:
    session = _session()
    stops: list[Decimal] = []
    for _ in range(10_000):
        if session.finished:
            break
        session.step()
        held = session._open  # pyright: ignore[reportPrivateUsage]
        if held is not None:
            stops.append(held.planned_stop)
    assert stops, "보유 중 스텝이 있어야 한다"
    # 🔴 손절이 초기값(95) 위로 트레일됐다.
    assert max(stops) > Decimal(95), "오르는 SMA 로 손절이 상향돼야 한다"
    # 🔴 하향 없음 (규칙 #3) — 단조 비감소.
    assert all(b >= a for a, b in pairwise(stops)), "손절은 내려가면 안 된다"
