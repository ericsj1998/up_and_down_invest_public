"""flip_on_engulf — 보유 중 반대 장악/도지에 익절+반대 진입 (사용자 설계 2026-08-23).

막아야 하는 실패:
1. 🔴 스위치가 꺼져 있으면 한 비트도 안 달라진다 (측정 스위치 · 동결)
2. 🔴 뒤집은 포지션의 손절이 반전 캔들 반대 끝이 아니면 손익비 근거가 무너진다
3. 🔴 도지 판정이 몸통/범위 비율을 안 지키면 아무 봉이나 뒤집는다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.indicators.reversal import dragonfly, gravestone
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
TURN_AT = START + timedelta(hours=22)


def _candle(frame: Timeframe, ts: datetime) -> Candle:
    """레벨 위 양봉 · TURN_AT 부터 45분은 하락 장악(진입가 위 큰 음봉 셋)."""
    if TURN_AT <= ts < TURN_AT + timedelta(minutes=45):
        return Candle(
            instrument=BTC,
            timeframe=frame,
            ts=ts,
            open=Decimal(560),
            high=Decimal(562),
            low=Decimal(500),
            close=Decimal(505),
            volume=Decimal(10),
        )
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=Decimal(525),
        high=Decimal(540),
        low=Decimal(523),
        close=Decimal(538),
        volume=Decimal(10),
    )


def _session(*, flip: bool) -> Session:
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
    session.flip_on_engulf = flip
    record = TradeRecord(
        trade_id="t-long",
        playbook="box@0",
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(530),
        planned_stop=Decimal(487),
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


def _c(o: int, h: int, lo: int, cl: int) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M15,
        ts=START,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(cl),
        volume=Decimal(1),
    )


class TestDirectionalDoji:
    def test_gravestone_is_upper_wick(self) -> None:
        # 몸통 작고 위꼬리 김 (90~91 몸통, 91~110 위꼬리)
        assert gravestone(_c(90, 110, 89, 91)) and not dragonfly(_c(90, 110, 89, 91))

    def test_dragonfly_is_lower_wick(self) -> None:
        assert dragonfly(_c(109, 111, 90, 110)) and not gravestone(_c(109, 111, 90, 110))

    def test_spinning_both_wicks_is_neither(self) -> None:
        assert not gravestone(_c(100, 110, 90, 101)) and not dragonfly(_c(100, 110, 90, 101))


class TestFlip:
    def test_bearish_engulfing_exits_the_long_and_opens_a_short(self) -> None:
        session = _session(flip=True)
        closed = _run(session)
        assert closed and closed[0].outcome is Outcome.SIGNAL_EXIT, "롱이 전환 익절로 나가야 한다"
        held = session._open  # pyright: ignore[reportPrivateUsage]
        assert held is not None and held.direction is Direction.SHORT, "반대(숏)로 뒤집혀야 한다"
        assert held.planned_stop > held.entry, "숏 손절은 진입가 위 (반전 캔들 고점 근처)"

    def test_frozen_switch_holds_the_long(self) -> None:
        session = _session(flip=False)
        # 스위치 꺼짐 + 박스 플레이북은 hold_through_turn 없음 → 반익 뒤 신호 청산 경로.
        _run(session)
        held = session._open  # pyright: ignore[reportPrivateUsage]
        # 뒤집힌 숏은 없어야 한다 (있다면 flip 이 꺼졌는데 돈 것).
        assert held is None or held.direction is Direction.LONG
