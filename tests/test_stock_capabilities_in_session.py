"""T239 — 시장 능력표가 세션에 닿는가: 현물은 숏 없음·배율 없음 · 갭 손절은 시가 체결."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.types import Family, Playbook
from updown.apps.api.walkforward import apply_playbook_knobs
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.decision.risk.policy import RiskConfigError
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

AAPL = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)
BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 5, tzinfo=UTC)
STOP = Decimal(487)
GAP_AT = START + timedelta(hours=22)


def _candle(frame: Timeframe, ts: datetime, *, shape: str) -> Candle:
    """22시 15분 봉 — gap: 손절선(487) 아래 480 에서 연다 · wick: 꼬리만 485 · flat: 위."""
    if shape == "wick" and GAP_AT <= ts < GAP_AT + timedelta(minutes=15):
        return Candle(
            instrument=AAPL,
            timeframe=frame,
            ts=ts,
            open=Decimal(525),
            high=Decimal(530),
            low=Decimal(485),
            close=Decimal(528),
            volume=Decimal(10),
        )
    if shape == "gap" and GAP_AT <= ts < GAP_AT + timedelta(minutes=15):
        return Candle(
            instrument=AAPL,
            timeframe=frame,
            ts=ts,
            open=Decimal(480),
            high=Decimal(486),
            low=Decimal(478),
            close=Decimal(484),
            volume=Decimal(10),
        )
    return Candle(
        instrument=AAPL,
        timeframe=frame,
        ts=ts,
        open=Decimal(525),
        high=Decimal(538),
        low=Decimal(522),
        close=Decimal(535),
        volume=Decimal(10),
    )


def _book(stop_mode: str = "touch") -> Playbook:
    return Playbook(
        playbook_id="stock-sm",
        version="0",
        market_groups=(MarketGroup.FOREIGN_STOCK,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
        hold_through_turn=True,
        stop_mode=stop_mode,
    )


def _session(instrument: Instrument, *, shape: str, leverage: Decimal = Decimal(1)) -> Session:
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
        instrument=instrument,
        playbooks=(_book(),),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000), leverage=leverage),
    )
    record = TradeRecord(
        trade_id="t-gap",
        playbook=_book().attribution,
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


class TestGapStopFill:
    def test_gap_through_the_stop_fills_at_the_open(self) -> None:
        closed = _run(_session(AAPL, shape="gap"))
        assert [item.outcome for item in closed] == [Outcome.STOP_LOSS]
        assert closed[0].exit_price == Decimal(480), "손절선(487)이 아니라 시가(480)"

    def test_touch_without_gap_fills_at_the_stop(self) -> None:
        """저가만 손절선 아래로 내려간 봉 — 시가는 위 → 손절선 가격 그대로."""
        closed = _run(_session(AAPL, shape="wick"))
        assert closed and closed[0].exit_price == STOP


class TestCapabilitiesReachTheSession:
    def test_stock_market_blocks_shorts_and_leverage(self) -> None:
        session = _session(AAPL, shape="flat")
        apply_playbook_knobs(session, _book(), load_rules())
        assert session.short_allowed is False
        with pytest.raises(RiskConfigError):
            apply_playbook_knobs(
                _session(AAPL, shape="flat", leverage=Decimal(2)), _book(), load_rules()
            )

    def test_perp_market_allows_shorts(self) -> None:
        session = _session(BTC, shape="flat", leverage=Decimal(6))
        apply_playbook_knobs(session, _book(), load_rules())
        assert session.short_allowed is True
