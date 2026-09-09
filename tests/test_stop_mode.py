"""T233 ② — 매매법 선언 `stop_mode` (close | touch) 와 보호 손절.

close 는 봉인 백테스트가 T50 이후 늘 하던 몸통 확인 판정이고, touch 는 닿으면 그 가격이다. 라이브는
close 매매법이면 손절선을 거래소에 미러하지 않고 **보호 손절**(청산 거리의 `stop_protect_ratio`)만
건다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.playbook.select import (  # pyright: ignore[reportPrivateUsage]
    PlaybookConfigError,
    _stop_mode,  # pyright: ignore[reportPrivateUsage]
)
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
from updown.decision.sizing import MAINTENANCE_MARGIN, protect_stop
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
STOP = Decimal(487)
TOUCH_AT = START + timedelta(hours=22)


def _candle(frame: Timeframe, ts: datetime) -> Candle:
    """22시 15분 동안만 꼬리가 손절선 아래(485) — 몸통은 위."""
    low = Decimal(485) if TOUCH_AT <= ts < TOUCH_AT + timedelta(minutes=15) else Decimal(522)
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=Decimal(525),
        high=Decimal(538),
        low=low,
        close=Decimal(535),
        volume=Decimal(10),
    )


def _book(stop_mode: str) -> Playbook:
    return Playbook(
        playbook_id=f"sm-{stop_mode}",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
        hold_through_turn=True,
        stop_mode=stop_mode,
    )


def _session(book: Playbook, *, direction: Direction = Direction.LONG) -> Session:
    minutes = 60 * 26
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(minutes // 5)
        ],
        Timeframe.M15: [
            _candle(Timeframe.M15, START + timedelta(minutes=15 * i)) for i in range(minutes // 15)
        ],
    }
    seal = Seal(start=START + timedelta(hours=20), end=START + timedelta(hours=24))
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000), leverage=Decimal(6)),
    )
    stop = STOP if direction is Direction.LONG else Decimal(573)
    record = TradeRecord(
        trade_id="t-sm",
        playbook=book.attribution,
        actor=Actor.SYSTEM,
        direction=direction,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(530),
        planned_stop=stop,
        planned_target=Decimal(900) if direction is Direction.LONG else Decimal(300),
        planned_first=Decimal(900) if direction is Direction.LONG else Decimal(300),
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


class TestDeclaration:
    def test_accepts_close_and_touch_any_case(self) -> None:
        assert _stop_mode("close", "x") == "close"
        assert _stop_mode(" TOUCH ", "x") == "touch"

    def test_rejects_anything_else(self) -> None:
        with pytest.raises(PlaybookConfigError):
            _stop_mode("wick", "x")

    def test_default_is_close(self) -> None:
        assert _book("close").stop_mode == "close"


class TestSessionJudgement:
    """선언이 세션의 손절 판정을 가른다 — 측정 스위치 `stop_at_price` 없이."""

    def test_touch_playbook_exits_on_the_wick(self) -> None:
        closed = _run(_session(_book("touch")))
        assert [item.outcome for item in closed] == [Outcome.STOP_LOSS]
        assert closed[0].exit_price == STOP

    def test_close_playbook_ignores_the_wick(self) -> None:
        assert _run(_session(_book("close"))) == []


class TestGuardPrice:
    """라이브가 거래소에 걸 자리 — touch 는 손절선, close 는 보호 손절."""

    def test_touch_mirrors_the_planned_stop(self) -> None:
        session = _session(_book("touch"))
        session.stop_protect_ratio = Decimal("0.70")
        record = session.ledger.records[0]
        assert session.guard_price(record) == STOP

    def test_close_uses_the_protect_ratio_of_the_liquidation_distance(self) -> None:
        session = _session(_book("close"))
        session.stop_protect_ratio = Decimal("0.70")
        record = session.ledger.records[0]
        room = Decimal(1) / Decimal(6) - MAINTENANCE_MARGIN
        expected = Decimal(530) * (Decimal(1) - Decimal("0.70") * room)
        assert session.guard_price(record) == expected
        assert expected < STOP, "보호 손절은 정상 손절보다 먼 자리다"

    def test_short_is_mirrored(self) -> None:
        session = _session(_book("close"), direction=Direction.SHORT)
        session.stop_protect_ratio = Decimal("0.70")
        record = session.ledger.records[0]
        assert session.guard_price(record) > Decimal(573)

    def test_close_without_ratio_falls_back_to_the_planned_stop(self) -> None:
        """설정이 빠지면 무방비가 아니라 터치 손절(1.5.0 동작)이다 — 누락은 판을 띄울 때 막는다."""
        session = _session(_book("close"))
        assert session.guard_price(session.ledger.records[0]) == STOP

    def test_never_tighter_than_the_planned_stop(self) -> None:
        """손절선이 보호 자리보다 이미 멀면 손절선을 쓴다 — 조이는 쪽으로 안 간다."""
        assert protect_stop(
            entry=Decimal(100),
            stop=Decimal(50),
            leverage=Decimal(6),
            ratio=Decimal("0.7"),
            short=False,
        ) == Decimal(50)
