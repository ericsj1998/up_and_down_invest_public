"""세션 `entry_legs` 스위치 (T758 U3) — 분기 목록 밖 다리는 신규 진입만 막고 나머지는 그대로."""

from __future__ import annotations

from dataclasses import replace
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
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.session import Snapshot

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)


def book(name: str) -> Playbook:
    return Playbook(
        playbook_id=name,
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
        hold_through_turn=True,
        full_ride=True,
        stop_mode="touch",
        share_same_side=True,
    )


LEG_A = book("leg_a")
LEG_B = book("leg_b")


def flat_session(books: tuple[Playbook, ...]) -> Session:
    span = 60 * 6

    def bar(frame: Timeframe, ts: datetime) -> Candle:
        v = Decimal(100)
        return Candle(
            instrument=BTC, timeframe=frame, ts=ts, open=v, high=v + 1, low=v - 1, close=v,
            volume=Decimal(10),
        )  # fmt: skip

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
        ledger=Ledger(seed_cash=Decimal(10_000), leverage=Decimal(6)),
    )
    for _ in range(20):
        s.step()
    return s


def setup_long(stop: str = "94") -> TradeSetup:
    return TradeSetup(
        setup_type="test",
        rule_version="1.0",
        entry_trigger=EntryTrigger.TOUCH,
        entry_plan=(EntryLeg(price=Decimal(100), ratio=Decimal(1)),),
        avg_entry=Decimal(100),
        stop_loss=Decimal(stop),
        stop_candidates=(
            StopCandidate(price=Decimal(stop), timeframe=Timeframe.M15, source="test"),
        ),
        tp_ladder=(TakeProfitStep(price=Decimal(130), ratio=Decimal(1), then=None),),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
        rr_ratio=Decimal(3),
        confidence=0.5,
        evidence=(),
    )


def offer(s: Session, *owners: Playbook):  # type: ignore[no-untyped-def]
    shot = Snapshot(
        at=s.cursor,
        proposals=tuple(Proposal(playbook=o, setup=setup_long()) for o in owners),
        trend={},
        has_box=False,
    )
    return s._enter(shot)  # pyright: ignore[reportPrivateUsage]


def test_none_means_no_limit() -> None:
    s = flat_session((LEG_A, LEG_B))
    assert s.entry_legs is None
    got = offer(s, LEG_A)
    assert got is not None
    assert got.playbook == LEG_A.attribution
    assert s.entry_legs_blocked == 0


def test_leg_outside_quarter_list_does_not_enter_and_is_counted() -> None:
    s = flat_session((LEG_A, LEG_B))
    s.entry_legs = frozenset({"leg_b"})
    assert offer(s, LEG_A) is None
    assert s.entry_legs_blocked == 1
    assert not s.ledger.records


def test_other_leg_of_same_symbol_still_enters() -> None:
    s = flat_session((LEG_A, LEG_B))
    s.entry_legs = frozenset({"leg_b"})
    got = offer(s, LEG_B)
    assert got is not None
    assert got.playbook == LEG_B.attribution


def test_choice_equals_session_without_the_blocked_leg() -> None:
    """R2' 조건 — 막힌 다리가 앞에 와도 고르는 것은 그 다리가 없는 세션과 같다."""
    gated = flat_session((LEG_A, LEG_B))
    gated.entry_legs = frozenset({"leg_b"})
    alone = flat_session((LEG_B,))
    a = offer(gated, LEG_A, LEG_B)
    b = offer(alone, LEG_B)
    assert a is not None and b is not None
    assert (a.playbook, a.entry, a.planned_stop, a.leverage) == (
        b.playbook,
        b.entry,
        b.planned_stop,
        b.leverage,
    )


def test_empty_set_blocks_every_new_entry_but_keeps_open_position() -> None:
    s = flat_session((LEG_A,))
    held = offer(s, LEG_A)
    assert held is not None
    s.entry_legs = frozenset()
    assert offer(s, LEG_A) is None
    s.step()  # 보유 판은 계속 걷는다 — 막힌 것은 신규 진입뿐
    still = [r for r in s.ledger.records if r.trade_id == held.trade_id]
    assert still and still[0].outcome is Outcome.OPEN
    assert replace(still[0]).planned_stop == held.planned_stop
