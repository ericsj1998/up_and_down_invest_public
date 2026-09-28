"""T320 P2 — 한 종목 포지션을 같은 방향 다리 여럿이 **몫**으로 나눠 쓰는 세션.

지키는 것:

- 몫이 0 · 1개면 `_open` 은 예전 한 칸과 한 글자도 다르지 않다(쓰면 통째로 바뀐다).
- 몫이 여럿인데 어느 다리인지 모르는 채 읽거나 쓰면 **멈춘다** — 조용히 하나를 고르면 다른 다리의
  손절 · 청산을 건드린다(규칙 #8).
- 겹침은 **양쪽 다리 모두** `share_same_side` 이고 **같은 방향**일 때만 · 같은 다리는 하나 ·
  반대 방향은 막는다.
- 몫마다 **자기 손절**로 닫힌다 — 한 몫의 손절이 다른 몫을 닫지 않는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

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
    StopCandidate,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.orchestration.playbook_run import Proposal
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.session import Snapshot

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
HOURS = 26
TOP = Decimal(1000)


def price(minutes: int) -> Decimal:
    """그 분에 끝나는 봉의 종가 — 매시간 10 씩 내린다."""
    return TOP - Decimal(10) * Decimal(minutes) / Decimal(60)


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


def book(name: str, *, share: bool) -> Playbook:
    """탐지는 안 도는 매매법 — 닿으면 손절(touch)만 도는 다리."""
    return Playbook(
        playbook_id=name,
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
        hold_through_turn=True,
        full_ride=True,
        stop_mode="touch",
        share_same_side=share,
    )


BREAKOUT = book("breakout_long", share=True)
CHANNEL = book("daily_channel", share=True)
LONE = book("lone_leg", share=False)


def session(books: tuple[Playbook, ...]) -> Session:
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
    return Session(
        instrument=BTC,
        playbooks=books,
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )


def record(
    owner: Playbook, trade_id: str, *, stop: str, direction: Direction = Direction.LONG
) -> TradeRecord:
    long = direction is Direction.LONG
    return TradeRecord(
        trade_id=trade_id,
        playbook=owner.attribution,
        actor=Actor.SYSTEM,
        direction=direction,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(800),
        planned_stop=Decimal(stop),
        planned_target=Decimal(5000) if long else Decimal(10),
        planned_first=Decimal(5000) if long else Decimal(10),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )


def holding(s: Session, *items: TradeRecord) -> Session:
    for item in items:
        s.ledger.add(item)
        s._hold(item)  # pyright: ignore[reportPrivateUsage]
    return s


class TestOneShareIsTheOldSlot:
    def test_writing_replaces_like_before(self) -> None:
        """🔴 커서가 없으면 쓰기는 예전처럼 통째로 바뀐다 — 반전 뒤 새 방향 · 이어받기가 그대로."""
        s = session((BREAKOUT, LONE))
        a = record(BREAKOUT, "a", stop="700")
        b = record(LONE, "b", stop="700", direction=Direction.SHORT)
        s._open = a  # pyright: ignore[reportPrivateUsage]
        s._open = b  # pyright: ignore[reportPrivateUsage]
        assert s.positions == (b,)
        assert s.position is b
        s._open = None  # pyright: ignore[reportPrivateUsage]
        assert s.positions == () and s.position is None


class TestWhoMayShare:
    def test_two_sharing_legs_same_direction(self) -> None:
        s = holding(session((BREAKOUT, CHANNEL)), record(BREAKOUT, "a", stop="700"))
        assert s._may_share(CHANNEL, Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        assert s._share_room()  # pyright: ignore[reportPrivateUsage]

    def test_opposite_direction_is_blocked(self) -> None:
        """⛔ Gate 한 방향 모드 — 롱과 숏은 상쇄된다."""
        s = holding(session((BREAKOUT, CHANNEL)), record(BREAKOUT, "a", stop="700"))
        assert not s._may_share(CHANNEL, Direction.SHORT)  # pyright: ignore[reportPrivateUsage]

    def test_both_legs_must_declare_it(self) -> None:
        s = holding(session((BREAKOUT, LONE)), record(BREAKOUT, "a", stop="700"))
        assert not s._may_share(LONE, Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        t = holding(session((LONE, CHANNEL)), record(LONE, "a", stop="700"))
        assert not t._may_share(CHANNEL, Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        assert not t._share_room()  # pyright: ignore[reportPrivateUsage]

    def test_the_same_leg_is_one_share(self) -> None:
        s = holding(session((BREAKOUT, CHANNEL)), record(BREAKOUT, "a", stop="700"))
        assert not s._may_share(BREAKOUT, Direction.LONG)  # pyright: ignore[reportPrivateUsage]

    def test_an_empty_symbol_is_always_open(self) -> None:
        assert session((LONE,))._may_share(LONE, Direction.SHORT)  # pyright: ignore[reportPrivateUsage]


class TestTwoShares:
    def test_both_are_held(self) -> None:
        a, c = record(BREAKOUT, "a", stop="700"), record(CHANNEL, "c", stop="600")
        s = holding(session((BREAKOUT, CHANNEL)), a, c)
        assert {item.trade_id for item in s.positions} == {"a", "c"}
        assert not s._share_room()  # pyright: ignore[reportPrivateUsage]

    def test_guessing_which_share_is_refused(self) -> None:
        """🔴 어느 몫인지 모르는 채 고르지 않는다 — 멈춘다."""
        s = holding(
            session((BREAKOUT, CHANNEL)),
            record(BREAKOUT, "a", stop="700"),
            record(CHANNEL, "c", stop="600"),
        )
        with pytest.raises(RuntimeError):
            _ = s.position
        with pytest.raises(RuntimeError):
            s._open = None  # pyright: ignore[reportPrivateUsage]

    def test_the_cursor_reads_and_writes_one_share(self) -> None:
        a, c = record(BREAKOUT, "a", stop="700"), record(CHANNEL, "c", stop="600")
        s = holding(session((BREAKOUT, CHANNEL)), a, c)
        with s._on_leg(CHANNEL.attribution):  # pyright: ignore[reportPrivateUsage]
            assert s._open is c  # pyright: ignore[reportPrivateUsage]
            s._open = replace(c, planned_stop=Decimal(650))  # pyright: ignore[reportPrivateUsage]
            with pytest.raises(RuntimeError):
                s._open = a  # pyright: ignore[reportPrivateUsage]  — 다른 다리를 쓰려 한다
        held = {item.trade_id: item for item in s.positions}
        assert held["c"].planned_stop == Decimal(650) and held["a"] is a

    def test_each_share_stops_on_its_own_line(self) -> None:
        """🔴 한 몫의 손절이 다른 몫을 닫지 않는다 — 값이 내려가 790 이 먼저 닿는다."""
        a, c = record(BREAKOUT, "a", stop="790"), record(CHANNEL, "c", stop="500")
        s = holding(session((BREAKOUT, CHANNEL)), a, c)
        closed: list[TradeRecord] = []
        for _ in range(12 * 3):
            shot = s.step()
            if shot is not None:
                closed.extend(shot.closed)
            if closed:
                break
        assert [item.trade_id for item in closed] == ["a"]
        assert closed[0].outcome is Outcome.STOP_LOSS
        assert s.position is not None and s.position.trade_id == "c", (
            "남은 몫은 하나 — 예전처럼 읽힌다"
        )


def flat_session(books: tuple[Playbook, ...]) -> Session:
    """값이 100 언저리로 평탄한 창 — 캔들 문법 필터가 걸리지 않는다(`test_short_and_leverage`)."""
    span = 60 * 6

    def bar(frame: Timeframe, ts: datetime) -> Candle:
        v = Decimal(100)
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

    source = {
        Timeframe.M5: [
            bar(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(span // 5)
        ],
        Timeframe.M15: [
            bar(Timeframe.M15, START + timedelta(minutes=15 * i)) for i in range(span // 15)
        ],
    }
    s = Session(
        instrument=BTC,
        playbooks=books,
        feed=SealedFeed(source, Seal(start=START, end=START + timedelta(hours=6))),
        ledger=Ledger(seed_cash=Decimal(10_000), leverage=Decimal(3)),
    )
    for _ in range(20):  # 마감된 봉이 생기게 커서를 민다 — 탐지기가 없어 진입은 안 난다
        s.step()
    return s


def setup(*, long: bool) -> TradeSetup:
    """손절이 진입(100) 아래면 롱 · 위면 숏."""
    stop = Decimal(90) if long else Decimal(110)
    return TradeSetup(
        setup_type="test",
        rule_version="1.0",
        entry_trigger=EntryTrigger.TOUCH,
        entry_plan=(EntryLeg(price=Decimal(100), ratio=Decimal(1)),),
        avg_entry=Decimal(100),
        stop_loss=stop,
        stop_candidates=(StopCandidate(price=stop, timeframe=Timeframe.M15, source="test"),),
        tp_ladder=(
            TakeProfitStep(
                price=Decimal(130) if long else Decimal(70), ratio=Decimal(1), then=None
            ),
        ),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
        rr_ratio=Decimal(3),
        confidence=0.5,
        evidence=(),
    )


def fast(name: str, *, share: bool) -> Playbook:
    return replace(book(name, share=share), timeframe=Timeframe.M15)


class TestEntries:
    """`_enter` — 보유 중이면 같은 방향 몫을 더할 수 있는 다리의 후보만 든다."""

    @staticmethod
    def _held_breakout() -> tuple[Session, Playbook, Playbook, Playbook]:
        brk, chan, lone = (
            fast("breakout_long", share=True),
            fast("daily_channel", share=True),
            fast("lone_leg", share=False),
        )
        s = flat_session((brk, chan, lone))
        first = replace(record(brk, "a", stop="90"), entry=Decimal(100), opened_at=s.cursor)
        holding(s, first)
        return s, brk, chan, lone

    @staticmethod
    def _offer(s: Session, owner: Playbook, *, long: bool) -> TradeRecord | None:
        shot = Snapshot(
            at=s.cursor,
            proposals=(Proposal(playbook=owner, setup=setup(long=long)),),
            trend={},
            has_box=False,
        )
        return s._enter(shot)  # pyright: ignore[reportPrivateUsage]

    def test_a_second_sharing_leg_adds_a_share(self) -> None:
        s, brk, chan, _ = self._held_breakout()
        got = self._offer(s, chan, long=True)
        assert got is not None and got.playbook == chan.attribution
        assert {item.playbook for item in s.positions} == {brk.attribution, chan.attribution}

    def test_a_leg_that_does_not_share_is_filtered(self) -> None:
        s, brk, _, lone = self._held_breakout()
        assert self._offer(s, lone, long=True) is None
        assert [item.playbook for item in s.positions] == [brk.attribution]

    def test_the_opposite_direction_is_filtered(self) -> None:
        s, brk, chan, _ = self._held_breakout()
        assert self._offer(s, chan, long=False) is None
        assert [item.playbook for item in s.positions] == [brk.attribution]


def test_the_live_runner_refuses_shared_legs_until_p3() -> None:
    """🔴 러너 주문은 아직 포지션 전체를 겨눈다 — 한 몫 손절이 다른 몫까지 닫는다(T320 P3 전)."""
    from typing import Any, cast

    from updown.orchestration.walkforward.live_runner import LiveRunner

    s = session((BREAKOUT, CHANNEL))
    none = cast("Any", None)
    with pytest.raises(ValueError, match="share_same_side"):
        LiveRunner(s, cast("Any", s.feed), none, none, none)
