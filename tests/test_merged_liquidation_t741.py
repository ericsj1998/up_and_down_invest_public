"""T741 — 둘째 몫 · 불타기가 들기 전 합친 청산가 검사 (spec §4.2 ②).

지키는 것:

- 식은 `decision/` 순수 함수 하나다 — 세션 · 러너는 든 몫 · 새 몫을 모아 묻기만 한다.
- 든 몫이 없으면(첫 몫) 세션은 아무것도 하지 않는다 — β 가 지킨다.
- 어기면 **버퍼를 지키는 최대 크기**로 줄이고, 0 이면 막는다. 배율은 건드리지 않는다.
- 든 몫만으로 이미 위반이면 막는다 — 더 사서 고치지 않는다(규칙 #8-1).
- 숏은 거울이다. 계단식 유지증거금은 닿을 수 있는 가장 높은 단으로 센다(보수적).
- 둘째 몫과 불타기는 **같은 꼴**이다 — 한 검사 함수가 둘 다 본다(사용자 결정 2026-11-01).
- T739 DOGE dch 2024-10 꼴(낮게 든 이긴 몫 위에 높은 몫이 얹혀 합친 청산가가 앞 몫 손절 안쪽)을
  재현하면 문이 그 상태를 만들지 않는다.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

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
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopCandidate,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.decision.risk.merged_liquidation import (
    MergedLeg,
    check_merged_liquidation,
    maintenance_rate_for,
    merged_liquidation,
)
from updown.decision.risk.policy import RiskConfigError, load_settings, parse_settings
from updown.orchestration.playbook_run import Proposal
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import LiveRunner
from updown.orchestration.walkforward.session import Snapshot

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
L6 = Decimal(6)
B20 = Decimal("0.20")
MMR = Decimal("0.005")


def _keeps_buffer(legs: list[MergedLeg], *, long: bool, buffer: Decimal = B20) -> bool:
    """정의 그대로 — 모든 손절이 합친 청산가에서 평단 쪽으로 버퍼만큼 떨어져 있나."""
    liq = merged_liquidation(legs, long=long, leverage=L6)
    assert liq is not None
    quantity = sum((leg.quantity for leg in legs), Decimal(0))
    average = sum((leg.quantity * leg.entry for leg in legs), Decimal(0)) / quantity
    line = liq + buffer * (average - liq)
    return all((leg.stop >= line) if long else (leg.stop <= line) for leg in legs)


class TestTheArithmetic:
    def test_one_leg_matches_the_gate_formula(self) -> None:
        """한 조각이면 Gate 식 그대로 — 평단 x (1 - 1/L) ÷ (1 - m)."""
        got = merged_liquidation(
            [MergedLeg(Decimal(1), Decimal(100), Decimal(95))], long=True, leverage=L6
        )
        assert got is not None
        assert abs(got - Decimal(100) * (1 - 1 / L6) / (1 - MMR)) < Decimal("1e-9")

    def test_no_liquidation_at_one_x(self) -> None:
        assert (
            merged_liquidation(
                [MergedLeg(Decimal(1), Decimal(100), Decimal(95))], long=True, leverage=Decimal(1)
            )
            is None
        )

    def test_ladder_picks_the_highest_reachable_step(self) -> None:
        ladder = (
            (Decimal(1000), Decimal("0.005")),
            (Decimal(5000), Decimal("0.01")),
            (Decimal(20000), Decimal("0.02")),
        )
        assert maintenance_rate_for(Decimal(500), ladder=ladder) == Decimal("0.005")
        assert maintenance_rate_for(Decimal(3000), ladder=ladder) == Decimal("0.01")
        assert maintenance_rate_for(Decimal(99999), ladder=ladder) == Decimal("0.02")
        assert maintenance_rate_for(Decimal(3000)) == MMR, "계단이 없으면 원장 상수"


class TestTheVerdict:
    HELD = (MergedLeg(Decimal(1), Decimal(80), Decimal(76)),)  # 낮게 든 몫 · 손절 -5%

    def test_a_first_share_is_allowed(self) -> None:
        """든 몫이 없으면 새 몫 자기 손절만 본다 — β 안쪽(6.5%)이면 그대로."""
        got = check_merged_liquidation(
            held=(),
            new=MergedLeg(Decimal(1), Decimal(100), Decimal(94)),
            long=True,
            leverage=L6,
            buffer_ratio=B20,
        )
        assert got.action == "allow" and got.quantity == 1 and got.liq_before is None

    def test_a_higher_second_share_is_shrunk_to_the_buffer(self) -> None:
        """🔴 T739 꼴 — 100 에 같은 명목을 얹으면 평단이 올라 합친 청산가가 앞 몫 손절 안쪽이다."""
        new = MergedLeg(Decimal("0.8"), Decimal(100), Decimal(94))
        assert not _keeps_buffer([*self.HELD, new], long=True), "검사 없이 얹으면 어긴다"
        got = check_merged_liquidation(
            held=self.HELD, new=new, long=True, leverage=L6, buffer_ratio=B20
        )
        assert got.action == "shrink" and 0 < got.quantity < new.quantity
        assert got.binding_stop == Decimal(76), "묶은 것은 낮게 든 몫의 손절이다"
        fitted = MergedLeg(got.quantity, new.entry, new.stop)
        assert _keeps_buffer([*self.HELD, fitted], long=True)
        bigger = MergedLeg(got.quantity * Decimal("1.01"), new.entry, new.stop)
        assert not _keeps_buffer([*self.HELD, bigger], long=True), (
            "최대 수량이다 — 조금만 더 사도 어긴다"
        )
        assert (
            got.liq_after is not None
            and got.liq_before is not None
            and got.liq_after > got.liq_before
        )

    def test_a_new_stop_inside_the_line_is_labelled_as_such(self) -> None:
        """🟡-1 — 든 1@80(손절 76) 아래 60 에 새 몫(손절 56.1)을 얹으면 막는 것은 맞지만 원인은
        든 몫이 아니라 **새 몫 손절이 합친 선 안쪽**이다. 라벨이 거짓 원인을 말하지 않는다."""
        got = check_merged_liquidation(
            held=self.HELD,
            new=MergedLeg(Decimal(1), Decimal(60), Decimal("56.1")),
            long=True,
            leverage=L6,
            buffer_ratio=B20,
        )
        assert got.action == "block" and got.why == "new_stop_violates"
        assert got.binding_stop == Decimal("56.1")

    def test_a_held_violation_blocks_instead_of_averaging_down(self) -> None:
        """🔴 든 몫만으로 이미 어기면 막는다 — 낮게 더 사서 평단을 내리는 길은 가지 않는다."""
        held = [MergedLeg(Decimal(1), Decimal(80), Decimal(68))]  # 6x 청산 67.0 · 80% 선 69.6 아래
        got = check_merged_liquidation(
            held=held,
            new=MergedLeg(Decimal(1), Decimal(60), Decimal(57)),
            long=True,
            leverage=L6,
            buffer_ratio=B20,
        )
        assert got.action == "block" and got.quantity == 0 and got.why == "held_violates"

    def test_the_short_mirror(self) -> None:
        held = [MergedLeg(Decimal(1), Decimal(120), Decimal(126))]
        new = MergedLeg(Decimal("1.2"), Decimal(100), Decimal(106))
        assert not _keeps_buffer([*held, new], long=False)
        got = check_merged_liquidation(
            held=held, new=new, long=False, leverage=L6, buffer_ratio=B20
        )
        assert got.action == "shrink" and got.binding_stop == Decimal(126)
        assert _keeps_buffer([*held, MergedLeg(got.quantity, new.entry, new.stop)], long=False)

    def test_a_steeper_maintenance_step_allows_less(self) -> None:
        new = MergedLeg(Decimal("0.8"), Decimal(100), Decimal(94))
        flat = check_merged_liquidation(
            held=self.HELD, new=new, long=True, leverage=L6, buffer_ratio=B20
        )
        steep = check_merged_liquidation(
            held=self.HELD,
            new=new,
            long=True,
            leverage=L6,
            buffer_ratio=B20,
            ladder=((Decimal(50), MMR), (Decimal(10000), Decimal("0.02"))),
        )
        assert steep.action == "shrink" and steep.quantity < flat.quantity

    def test_zero_buffer_is_just_outside_the_stop(self) -> None:
        """버퍼 0 이면 조건은 '청산가가 손절 바깥' 그 자체 — 최대 수량에서 청산가 = 손절."""
        new = MergedLeg(Decimal(2), Decimal(100), Decimal(94))
        got = check_merged_liquidation(
            held=self.HELD, new=new, long=True, leverage=L6, buffer_ratio=Decimal(0)
        )
        assert got.action == "shrink"
        assert got.liq_after is not None and abs(got.liq_after - Decimal(76)) < Decimal("1e-6")

    def test_a_far_lower_add_is_free(self) -> None:
        """값이 아래면 평단이 내려와 청산가도 멀어진다 — 손절이 멀리 있지 않은 한 묶이지 않는다."""
        got = check_merged_liquidation(
            held=self.HELD,
            new=MergedLeg(Decimal(1), Decimal(78), Decimal(76)),
            long=True,
            leverage=L6,
            buffer_ratio=B20,
        )
        assert got.action == "allow" and got.why == "ok"

    def test_no_leverage_is_off(self) -> None:
        got = check_merged_liquidation(
            held=self.HELD,
            new=MergedLeg(Decimal(1), Decimal(100), Decimal(94)),
            long=True,
            leverage=Decimal(1),
            buffer_ratio=B20,
        )
        assert got.action == "allow" and got.why == "off"

    def test_bad_inputs_raise(self) -> None:
        new = MergedLeg(Decimal(1), Decimal(100), Decimal(94))
        with pytest.raises(ValueError, match="buffer"):
            check_merged_liquidation(
                held=(), new=new, long=True, leverage=L6, buffer_ratio=Decimal(1)
            )
        with pytest.raises(ValueError, match="양수"):
            check_merged_liquidation(
                held=(), new=new, long=True, leverage=Decimal(0), buffer_ratio=B20
            )

    def test_doge_dch_2024_10_shape(self) -> None:
        """🔴 T739 대표 사건의 꼴 — 0.12571 에 든 이긴 dch 위에 돌파 롱이 높이 얹혀 합친 청산가가
        dch 손절 안쪽에 섰고 dch 는 자기 손절이 아니라 청산가(0.382)에서 끊겼다. 문이 있으면
        그 상태를 만들지 않는다."""
        dch = MergedLeg(Decimal(1000), Decimal("0.12571"), Decimal("0.1176"))  # β 6.5% 안쪽 손절
        breakout = MergedLeg(
            Decimal(1000) * Decimal("0.12571") / Decimal("0.47"), Decimal("0.47"), Decimal("0.44")
        )
        liq_unchecked = merged_liquidation([dch, breakout], long=True, leverage=L6)
        assert liq_unchecked is not None and liq_unchecked > dch.stop, (
            "검사 없이 얹으면 dch 손절은 장식이다"
        )
        got = check_merged_liquidation(
            held=[dch], new=breakout, long=True, leverage=L6, buffer_ratio=B20
        )
        assert got.action == "shrink" and got.binding_stop == dch.stop
        assert got.ratio < Decimal("0.15"), (
            "낮게 든 몫 위에 네 배 값으로 얹을 수 있는 것은 조금이다"
        )
        assert _keeps_buffer(
            [dch, MergedLeg(got.quantity, breakout.entry, breakout.stop)], long=True
        )


class TestThePolicyKnob:
    def _raw(self, value: object) -> dict[str, object]:
        return {
            "anchors": {
                "conservative": {
                    "risk_pct": 0.005,
                    "min_rr": 2.0,
                    "max_positions": 3,
                    "daily_loss_limit_pct": -0.02,
                },
                "standard": {
                    "risk_pct": 0.01,
                    "min_rr": 1.75,
                    "max_positions": 5,
                    "daily_loss_limit_pct": -0.03,
                },
                "aggressive": {
                    "risk_pct": 0.03,
                    "min_rr": 1.5,
                    "max_positions": 8,
                    "daily_loss_limit_pct": -0.06,
                },
            },
            "merged_liq_buffer_ratio": value,
        }

    def test_it_is_parsed(self) -> None:
        assert parse_settings(self._raw(0.2)).merged_liq_buffer_ratio == Decimal("0.2")
        assert (
            parse_settings(
                {k: v for k, v in self._raw(0).items() if k != "merged_liq_buffer_ratio"}
            ).merged_liq_buffer_ratio
            is None
        )

    def test_one_or_more_is_rejected(self) -> None:
        with pytest.raises(RiskConfigError, match="merged_liq_buffer_ratio"):
            parse_settings(self._raw(1.0))
        with pytest.raises(RiskConfigError, match="merged_liq_buffer_ratio"):
            parse_settings(self._raw(-0.1))

    def test_the_repository_config_turns_it_on_at_the_audit_line(self) -> None:
        """0.20 = 감사 ⑨ 가 '손절이 청산거리의 80% 지점' 을 경고하는 선."""
        assert load_settings().merged_liq_buffer_ratio == Decimal("0.20")


# ── 세션 — 둘째 몫 ────────────────────────────────────────────────────────────


def book(name: str, *, share: bool) -> Playbook:
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
        share_same_side=share,
    )


BREAKOUT = book("breakout_long", share=True)
CHANNEL = book("daily_channel", share=True)


def flat_session(books: tuple[Playbook, ...], *, leverage: Decimal = L6) -> Session:
    """값이 100 으로 평탄한 창 — 캔들 문법 필터가 걸리지 않는다(`test_session_shares` 와 같다)."""
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
        ledger=Ledger(seed_cash=Decimal(10_000), leverage=leverage),
    )
    for _ in range(20):
        s.step()
    return s


def held_record(owner: Playbook, *, entry: str, stop: str, at: datetime) -> TradeRecord:
    return TradeRecord(
        trade_id="a",
        playbook=owner.attribution,
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=at,
        opened_at=at,
        entry=Decimal(entry),
        planned_stop=Decimal(stop),
        planned_target=Decimal(5000),
        planned_first=Decimal(5000),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
        leverage=L6,
    )


def setup_long(stop: str) -> TradeSetup:
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


def setup_short(stop: str) -> TradeSetup:
    """손절이 진입(100) 위 → 숏. 목표는 아래."""
    return replace(
        setup_long(stop),
        stop_loss=Decimal(stop),
        stop_candidates=(
            StopCandidate(price=Decimal(stop), timeframe=Timeframe.M15, source="test"),
        ),
        tp_ladder=(TakeProfitStep(price=Decimal(70), ratio=Decimal(1), then=None),),
    )


def offer(s: Session, owner: Playbook, stop: str = "94") -> TradeRecord | None:
    shot = Snapshot(
        at=s.cursor,
        proposals=(Proposal(playbook=owner, setup=setup_long(stop)),),
        trend={},
        has_box=False,
    )
    return s._enter(shot)  # pyright: ignore[reportPrivateUsage]


def holding(
    s: Session, *, entry: str, stop: str, direction: Direction = Direction.LONG, half: bool = False
) -> Session:
    first = held_record(BREAKOUT, entry=entry, stop=stop, at=s.cursor)
    if direction is Direction.SHORT:
        first = replace(
            first, direction=direction, planned_target=Decimal(1), planned_first=Decimal(1)
        )
    if half:
        first = replace(first, half_at=s.cursor, half_price=Decimal(entry))
    s.ledger.add(first)
    s._hold(first)  # pyright: ignore[reportPrivateUsage]
    return s


class TestTheSessionAsksBeforeASecondShare:
    def test_a_second_share_is_shrunk(self) -> None:
        """80 에 든 몫(손절 76) 위에 100 에 같은 노출을 얹으면 — 노출이 줄고 깔때기에 남는다."""
        s = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="76")
        s.merged_liq_buffer_ratio = B20
        got = offer(s, CHANNEL)
        assert got is not None and got.leverage < L6
        assert s.funnel.get("merged_liq:shrink") == 1
        held = {item.playbook: item for item in s.positions}
        assert held[BREAKOUT.attribution].leverage == L6, "든 몫은 그대로다"

    def test_a_held_violation_blocks_the_second_share(self) -> None:
        s = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="68")
        s.merged_liq_buffer_ratio = B20
        assert offer(s, CHANNEL) is None
        assert s.funnel.get("merged_liq:block") == 1
        assert [item.playbook for item in s.positions] == [BREAKOUT.attribution]

    def test_without_the_knob_nothing_changes(self) -> None:
        s = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="76")
        got = offer(s, CHANNEL)
        assert got is not None and got.leverage == L6
        assert "merged_liq:shrink" not in s.funnel

    def test_a_first_share_is_untouched(self) -> None:
        """🔴 단일 몫은 영향 없음 — 든 몫이 없으면 검사 자체가 돌지 않는다."""
        s = flat_session((BREAKOUT, CHANNEL))
        s.merged_liq_buffer_ratio = B20
        got = offer(s, BREAKOUT)
        assert got is not None and got.leverage == L6
        assert not any(key.startswith("merged_liq") for key in s.funnel)

    def test_no_liquidation_market_is_untouched(self) -> None:
        s = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="76")
        s.merged_liq_buffer_ratio = B20
        s.has_liquidation = False
        got = offer(s, CHANNEL)
        assert got is not None and got.leverage == L6

    def test_the_short_mirror_in_the_session(self) -> None:
        """숏 — 120 에 든 몫(손절 126) 아래 100 에 같은 노출의 숏을 얹으면 평단이 내려와 합친
        청산가가 든 몫 손절 안쪽이다 → 줄인다."""
        s = holding(
            flat_session((BREAKOUT, CHANNEL)), entry="120", stop="126", direction=Direction.SHORT
        )
        s.merged_liq_buffer_ratio = B20
        shot = Snapshot(
            at=s.cursor,
            proposals=(Proposal(playbook=CHANNEL, setup=setup_short("106")),),
            trend={},
            has_box=False,
        )
        got = s._enter(shot)  # pyright: ignore[reportPrivateUsage]
        assert got is not None and got.direction is Direction.SHORT and got.leverage < L6
        assert s.funnel.get("merged_liq:shrink") == 1

    def test_a_half_closed_share_counts_half(self) -> None:
        """반익한 몫은 수량 절반 — 든 몫이 작으면 같은 둘째 몫이 평단을 더 끌어 더 줄어든다."""
        whole = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="76")
        whole.merged_liq_buffer_ratio = B20
        halved = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="76", half=True)
        halved.merged_liq_buffer_ratio = B20
        legs_whole = whole._merged_legs()  # pyright: ignore[reportPrivateUsage]
        legs_half = halved._merged_legs()  # pyright: ignore[reportPrivateUsage]
        assert legs_half[0].quantity == legs_whole[0].quantity / 2
        a, b = offer(whole, CHANNEL), offer(halved, CHANNEL)
        assert a is not None and b is not None and b.leverage < a.leverage < L6

    def test_the_limit_ladder_path_is_fitted_too(self) -> None:
        """`_bid`(지정가) — 시장가와 같은 자에서 줄인다. 대기 기록의 노출이 줄어 있다."""
        from updown.orchestration.walkforward.sealed_filler import SealedFiller

        s = holding(flat_session((BREAKOUT, CHANNEL)), entry="80", stop="76")
        s.merged_liq_buffer_ratio = B20
        s.limit_entry = True
        s.filler = SealedFiller()
        assert offer(s, CHANNEL) is None, "지정가는 걸어 두는 것이라 바로 산 기록이 없다"
        pending = s.pending_entry()
        assert pending is not None and pending[0].leverage < L6
        assert s.funnel.get("merged_liq:shrink") == 1


# ── 러너 — 불타기 · 둘째 몫 계약 수 ────────────────────────────────────────


NOW = datetime.now(UTC)
SOL = Instrument(Market.GATE, "SOL_USDT", "솔라나", AssetType.COIN, Currency.USD)


def _open(stop: str, **kw: Any) -> TradeRecord:
    base: dict[str, Any] = {
        "trade_id": "8966b6c99ac5",
        "playbook": "private_strategy@0.1.0",
        "actor": Actor.SYSTEM,
        "direction": Direction.LONG,
        "placed_at": NOW - timedelta(hours=3),
        "opened_at": NOW - timedelta(hours=3),
        "entry": Decimal(100),
        "planned_stop": Decimal(stop),
        "planned_target": Decimal(500),
        "planned_first": Decimal(500),
        "outcome": Outcome.OPEN,
        "leverage": Decimal(4),
        "contracts": 4,
        "cost_pct": Decimal("0.001"),
        "add_at": NOW - timedelta(minutes=2),
        "add_price": Decimal(110),
        "add_frac": Decimal("0.5"),
        "add_exposure": Decimal(2),
    }
    return TradeRecord(**(base | kw))


class _Ledger:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.records = records
        self.sizing_base = Decimal(100)
        self.leverage = L6
        self.tripped_at: str | None = None

    def replace(self, record: TradeRecord) -> None:
        for index, item in enumerate(self.records):
            if item.trade_id == record.trade_id:
                self.records[index] = record
                return
        raise KeyError(record.trade_id)


class _Session:
    def __init__(
        self, records: list[TradeRecord], *, buffer: Decimal | None, shared: bool = False
    ) -> None:
        self.ledger = _Ledger(records)
        self.auto = True
        self.merged_liq_buffer_ratio = buffer
        self.playbooks = (BREAKOUT, CHANNEL) if shared else ()
        self.instrument = SOL
        self.released: list[str | None] = []

    def release(self, leg: str | None = None) -> None:
        self.released.append(leg)


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw.get("payload", {})))

    warning = info
    error = info


class _Exchange:
    def __init__(self, *, size: str = "4") -> None:
        self.size = size
        self.sent: list[OrderRequest] = []

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        _ = instrument
        return {"size": self.size, "leverage": "6"}

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        self.sent.append(order)
        return OrderResult(
            broker_order_id="42",
            idempotency_key="k",
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            average_price=Decimal(110),
            ts=NOW,
            reason=None,
        )


class _Runner:
    """불타기 · 검사 메서드가 만지는 것만 가진 러너 — 메서드는 진짜다(`test_live_add` 방식)."""

    _apply_add_one = LiveRunner._apply_add_one  # pyright: ignore[reportPrivateUsage]
    _shared = LiveRunner._shared  # pyright: ignore[reportPrivateUsage]
    _record_add = LiveRunner._record_add  # pyright: ignore[reportPrivateUsage]
    _drop_add = LiveRunner._drop_add  # pyright: ignore[reportPrivateUsage]
    _share_contracts = staticmethod(LiveRunner._share_contracts)  # pyright: ignore[reportPrivateUsage]
    _merged_liq_legs = LiveRunner._merged_liq_legs  # pyright: ignore[reportPrivateUsage]
    _merged_liq_contracts = LiveRunner._merged_liq_contracts  # pyright: ignore[reportPrivateUsage]
    _skip_merged_liq = LiveRunner._skip_merged_liq  # pyright: ignore[reportPrivateUsage]

    def __init__(
        self, records: list[TradeRecord], *, buffer: Decimal | None = B20, shared: bool = False
    ) -> None:
        self._session = _Session(records, buffer=buffer, shared=shared)
        self._orders = _Exchange()
        self.observe_only = False
        self.dry: dict[str, str] | None = None
        self.fund_ready = True
        self.instrument = SOL
        self._run_key = "6eccc5abcdef"
        self.orders = 0
        self.failures = 0
        self.last_error = ""
        self._log = _Log()
        self.fired: list[str] = []
        self.persisted = 0
        self.notes: list[dict[str, object]] = []
        self.placed: dict[str, dict[str, str]] = {}

    async def _contract_spec(self) -> dict[str, str]:
        return {
            "quanto_multiplier": "1",
            "order_size_min": "1",
            "order_size_max": "0",
            "maintenance_rate": "0.004",
        }

    async def _spare_margin(self) -> Decimal | None:
        return Decimal(1000)

    async def _persist(self) -> None:
        self.persisted += 1

    async def _note_order(self, trade_id: str, **kw: object) -> None:
        self.notes.append({"trade_id": trade_id, **kw})

    def _fired(self, name: str, detail: str) -> None:
        _ = detail
        self.fired.append(name)

    @property
    def record(self) -> TradeRecord:
        return self._session.ledger.records[0]

    @property
    def session(self) -> _Session:
        return self._session

    @property
    def sent(self) -> list[int]:
        return [int(order.quantity) for order in self._orders.sent]

    @property
    def events(self) -> list[str]:
        return [event for event, _ in self._log.events]

    def add(self) -> None:
        asyncio.run(self._apply_add_one(self.record))

    def skip(self, record: TradeRecord, detail: str) -> None:
        asyncio.run(self._skip_merged_liq(record, detail))

    def fit_share(self, record: TradeRecord, contracts: int) -> tuple[int, str] | None:
        return self._merged_liq_contracts(
            role="share",
            record=record,
            contracts=contracts,
            price=record.entry,
            stop=record.planned_stop,
            direction=record.direction,
            multiplier=Decimal(1),
            leverage=L6,
            spec={"quanto_multiplier": "1", "maintenance_rate": "0.004"},
            snapshot=None,
        )


class TestTheRunnerAsksBeforeAnAdd:
    """4 계약 @100 위에 2 계약 @110 을 얹는다(6x · 유지증거금 0.5%). 손절이 묶는 자리를 손으로 셌다:
    합친 청산가 86.5 · 평단 103.3 · 80% 선 89.9 → 손절 95 는 2 계약 · 89 는 1 계약 · 88 은 0."""

    def test_a_safe_stop_adds_everything(self) -> None:
        r = _Runner([_open("95")])
        r.add()
        assert r.sent == [2]
        assert r.record.add_contracts == 2 and "merged_liq" not in r.fired

    def test_a_close_stop_shrinks_the_add(self) -> None:
        r = _Runner([_open("89")])
        r.add()
        assert r.sent == [1], "버퍼를 지키는 최대 계약"
        assert r.record.add_contracts == 1 and "merged_liq" in r.fired
        assert r.events.index("live_merged_liq_check") < r.events.index("live_add_filled"), (
            "먼저 적고 그 다음 보낸다"
        )

    def test_a_too_close_stop_drops_the_add(self) -> None:
        r = _Runner([_open("88")])
        r.add()
        assert not r.sent
        assert r.record.add_held == "merged_liq" and r.record.add_exposure == 0

    def test_without_the_knob_nothing_changes(self) -> None:
        r = _Runner([_open("88")], buffer=None)
        r.add()
        assert r.sent == [2]

    def test_unknown_share_contracts_defer_the_add_not_drop_it(self) -> None:
        """🟠-3 — 몫 모드에서 계약 수를 모르는 몫이 있으면 추정하지 않고 **다음 걸음에 다시** 본다
        (`live_add_unreadable` 과 같은 태도 · 늦으면 `stale` 로 자연 소멸). 영구 버림이 아니다."""
        other = _open(
            "95", trade_id="other", playbook=CHANNEL.attribution, contracts=0, add_at=None
        )
        r = _Runner([_open("95"), other], shared=True)
        r._orders.size = "8"  # pyright: ignore[reportPrivateUsage]  — 거래소엔 두 몫 합이 들어 있다
        r.add()
        assert not r.sent
        assert r.record.add_held is None and r.record.add_exposure == 2, "버리지 않았다"
        assert "live_merged_liq_unreadable" in r.events
        # 사람이 계약 수를 채우면(또는 체결 기록이 붙으면) 같은 불타기가 그대로 나간다.
        r.session.ledger.replace(replace(other, contracts=4))
        r.add()
        assert r.sent == [2]

    def test_the_short_mirror_in_the_runner(self) -> None:
        """숏 4 계약 @100(손절 110) 위에 2 계약 @90 — 롱 손셈의 거울 · 1 계약만 허용된다."""
        short = _open(
            "110",
            direction=Direction.SHORT,
            add_price=Decimal(90),
            planned_target=Decimal(1),
            planned_first=Decimal(1),
        )
        r = _Runner([short])
        r._orders.size = "-4"  # pyright: ignore[reportPrivateUsage]
        r.add()
        assert r.sent == [1] and "merged_liq" in r.fired

    def test_a_half_closed_share_counts_half_in_the_runner(self) -> None:
        """반익한 몫은 `contracts // 2` 만 든 것으로 센다 — 조각 수량이 절반이다."""
        half = _open("95", add_at=None, half_at=NOW - timedelta(hours=1), half_price=Decimal(120))
        r = _Runner([half])
        legs = r._merged_liq_legs("", Decimal(1), None)  # pyright: ignore[reportPrivateUsage]
        assert legs is not None and [leg.quantity for leg in legs] == [Decimal(2)]


class TestTheRunnerAsksBeforeASecondShare:
    def test_the_second_share_is_shrunk_by_contracts(self) -> None:
        held = _open("89", add_at=None)  # 4 계약 @100
        new = _open(
            "94",
            trade_id="new",
            playbook=CHANNEL.attribution,
            contracts=0,
            add_at=None,
            entry=Decimal(110),
        )
        r = _Runner([held, new], shared=True)
        got = r.fit_share(new, 2)
        assert got is not None and got[0] == 1 and "shrunk" in got[1]

    def test_unknown_other_share_contracts_give_no_verdict(self) -> None:
        """다른 몫의 계약 수를 모르면 None — 둘째 몫 `_send` 는 그것을 막음으로 읽어 거둔다(D2)."""
        held = _open("89", add_at=None, contracts=0)
        new = _open("94", trade_id="new", playbook=CHANNEL.attribution, contracts=0, add_at=None)
        r = _Runner([held, new], shared=True)
        assert r.fit_share(new, 2) is None
        assert "live_merged_liq_unreadable" in r.events

    def test_a_blocked_second_share_is_withdrawn_from_the_ledger(self) -> None:
        held = _open("88", add_at=None)
        new = _open(
            "94",
            trade_id="new",
            playbook=CHANNEL.attribution,
            contracts=0,
            add_at=None,
            entry=Decimal(110),
        )
        r = _Runner([held, new], shared=True)
        r.skip(new, "시험")
        closed = {item.trade_id: item for item in r.session.ledger.records}["new"]
        assert closed.outcome is Outcome.CANCELLED
        assert r.placed["new"]["status"] == "blocked"
        assert r.session.released == [CHANNEL.attribution], "그 몫만 놓는다"
        assert r.notes and r.notes[0]["status"] == "blocked"


def test_the_spec_rule_is_named_in_the_check() -> None:
    """문서 래칫 — 식의 근거(spec §4.2 ②)가 함수 docstring 에 남아 있다."""
    assert check_merged_liquidation.__doc__ and "§4.2" in check_merged_liquidation.__doc__
