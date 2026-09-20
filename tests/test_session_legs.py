"""T291 — 한 세션에 시간축이 다른 두 매매법이 실릴 때의 세션 동작.

지키는 것:

- **청산 봉은 그 매매를 낸 매매법의 시간축에서 읽는다.** 대표(첫 매매법)의 축으로 읽으면 1H 숏이
  15m SMA 에서 닫힌다 — 다른 매매법이 된다.
- 다리 배율이 심겨 있으면 진입 노출을 그 다리의 배율로 바꾼다. 없으면 그대로(동결).
- 다리를 아는 문(`grant_for`)에는 다리를 알려 주고, 모르는 문에는 지금까지처럼 묻는다.
- 되읽은 포지션은 그 방향의 청산 규칙을 선언한 매매법에 귀속한다.
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
from updown.decision.portfolio_rules import Grant
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
HOURS = 26
TOP = Decimal(1000)
WOBBLE = (Decimal(-12), Decimal(-14), Decimal(-16), Decimal(-10))
"""한 시간 안의 15분 종가(시간 시작가 대비). 시간 종가는 매시간 10 씩 내린다 — 1H 종가는 늘 자기
SMA(3) **아래**인데, 15m 넷째 봉(-10)은 직전 둘(-14·-16)보다 높아 15m SMA(3) **위**로 마감한다."""


def price(minutes: int) -> Decimal:
    """그 분에 끝나는 15분 봉의 종가."""
    hour, quarter = divmod(minutes // 15, 4)
    return TOP - Decimal(10) * hour + WOBBLE[quarter]


def candle(frame: Timeframe, ts: datetime, close: Decimal) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=close + 1,
        high=close + 2,
        low=close - 1,
        close=close,
        volume=Decimal(10),
    )


def book(name: str, frame: Timeframe, *, short_exit: int | None = None) -> Playbook:
    """탐지는 안 도는 매매법 — 시간축과 숏 청산만 다르다."""
    return Playbook(
        playbook_id=name,
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=frame,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
        hold_through_turn=True,
        full_ride=True,
        stop_mode="touch",
        ma_exit_above_short=short_exit,
    )


FAST = book("fast_long", Timeframe.M15)
SLOW = book("slow_short", Timeframe.H1, short_exit=3)


def session_holding_a_short(owner: Playbook, books: tuple[Playbook, ...]) -> Session:
    source = {
        Timeframe.M5: [
            candle(Timeframe.M5, START + timedelta(minutes=5 * i), price(5 * i))
            for i in range(HOURS * 12)
        ],
        Timeframe.M15: [
            candle(Timeframe.M15, START + timedelta(minutes=15 * i), price(15 * i))
            for i in range(HOURS * 4)
        ],
        Timeframe.H1: [
            candle(Timeframe.H1, START + timedelta(hours=i), price(60 * i + 45))
            for i in range(HOURS)
        ],
    }
    seal = Seal(start=START + timedelta(hours=20), end=START + timedelta(hours=24))
    session = Session(
        instrument=BTC,
        playbooks=books,
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    record = TradeRecord(
        trade_id="t-short",
        playbook=owner.attribution,
        actor=Actor.SYSTEM,
        direction=Direction.SHORT,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(800),
        planned_stop=Decimal(2000),
        planned_target=Decimal(10),
        planned_first=Decimal(10),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return session


def closed_after_running(session: Session) -> list[TradeRecord]:
    for _ in range(10_000):
        if session.finished:
            break
        session.step()
    return [item for item in session.ledger.records if item.closed_at is not None]


class TestExitBarsComeFromTheTradesOwnPlaybook:
    def test_a_slow_short_ignores_the_fast_frames_average(self) -> None:
        """대표가 15m 매매법이어도 1H 숏은 **1H** SMA 로 판정한다 — 1H 종가는 늘 SMA 아래다."""
        session = session_holding_a_short(SLOW, (FAST, SLOW))
        assert closed_after_running(session) == []

    def test_the_same_rule_on_the_fast_frame_does_exit(self) -> None:
        """대조: 같은 청산을 15m 에서 읽으면 첫 시간 안에 나간다 — 위 시험이 공허하지 않다."""
        quick = book("quick_short", Timeframe.M15, short_exit=3)
        session = session_holding_a_short(quick, (quick,))
        closed = closed_after_running(session)
        assert len(closed) == 1 and closed[0].closed_at is not None
        assert closed[0].closed_at < START + timedelta(hours=21, minutes=5)


class TestLegLeverage:
    def test_no_declaration_changes_nothing(self) -> None:
        session = session_holding_a_short(SLOW, (FAST, SLOW))
        scaled = session._leg_scaled(Decimal(6), SLOW.attribution)  # pyright: ignore[reportPrivateUsage]
        assert scaled == Decimal(6)

    def test_a_smaller_leg_is_scaled_down_and_multipliers_survive(self) -> None:
        session = session_holding_a_short(SLOW, (FAST, SLOW))
        session.ledger.leverage = Decimal(4)
        session.leg_leverage = {FAST.attribution: Decimal(4), SLOW.attribution: Decimal(2)}
        scale = session._leg_scaled  # pyright: ignore[reportPrivateUsage]
        assert scale(Decimal(4), SLOW.attribution) == Decimal(2)
        assert scale(Decimal(6), FAST.attribution) == Decimal(6), "기울기 1.5 가 곱해진 롱은 그대로"
        assert scale(Decimal(2), SLOW.attribution) == Decimal(1), "승수 0.5 는 비율이라 남는다"

    def test_a_leg_never_exceeds_the_exchange_leverage(self) -> None:
        session = session_holding_a_short(SLOW, (FAST, SLOW))
        session.ledger.leverage = Decimal(2)
        session.leg_leverage = {SLOW.attribution: Decimal(5)}
        assert session._leg_scaled(Decimal(2), SLOW.attribution) == Decimal(2)  # pyright: ignore[reportPrivateUsage]


class TestTheGateHearsWhichLegAsks:
    def test_a_leg_aware_gate_is_asked_by_leg(self) -> None:
        heard: list[str] = []

        class Split:
            def grant(self, at: datetime, exposure: Decimal) -> Grant:  # noqa: ARG002
                return Grant(Decimal(0), "leg")

            def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:  # noqa: ARG002
                heard.append(leg)
                return Grant(exposure, None)

        session = session_holding_a_short(SLOW, (FAST, SLOW))
        session.entry_gate = Split()
        given = session._gate(START, Decimal(2), SLOW.attribution)  # pyright: ignore[reportPrivateUsage]
        assert given == Decimal(2) and heard == [SLOW.attribution]

    def test_a_plain_gate_is_asked_as_before(self) -> None:
        class Plain:
            def grant(self, at: datetime, exposure: Decimal) -> Grant:  # noqa: ARG002
                return Grant(exposure / 2, None, "notional")

        session = session_holding_a_short(SLOW, (FAST, SLOW))
        session.entry_gate = Plain()
        assert session._gate(START, Decimal(4), FAST.attribution) == Decimal(2)  # pyright: ignore[reportPrivateUsage]


class TestAdoptedPositionsGoToTheLegThatHoldsThatSide:
    def test_a_short_goes_to_the_short_leg(self) -> None:
        session = session_holding_a_short(SLOW, (FAST, SLOW))
        assert session.book_for(Direction.SHORT) is SLOW

    def test_nobody_declares_longs_so_the_representative_keeps_them(self) -> None:
        session = session_holding_a_short(SLOW, (FAST, SLOW))
        assert session.book_for(Direction.LONG) is FAST

    def test_a_single_playbook_is_always_itself(self) -> None:
        session = session_holding_a_short(SLOW, (SLOW,))
        assert session.book_for(Direction.LONG) is SLOW
