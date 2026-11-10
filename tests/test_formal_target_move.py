"""형식 목표를 가격 이동 비율로도 가른다 (T823 · `order_mapping.FORMAL_MOVE`).

숏 다리는 "목표 없음" 을 진입가 x 0.05 로 적는다. 리스크 배수(`SENTINEL_RR`)로만 가르면
손절이 1.9% 보다 넓은 매매에서 그 값이 진짜 목표로 읽혀 진입가의 5% 값에 지정가가 걸렸다.

지키려는 성질 셋.

1. 숏의 형식 목표는 손절 폭과 무관하게 형식 목표다(주문 없음 · 목표 감시 없음).
2. 진짜 목표(2R · 2.5R · 손절이 넓은 경우 포함)는 그대로 진짜 목표다.
3. 롱 타기 다리의 리스크 100 배 목표는 전처럼 형식 목표다.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.orchestration.walkforward.ledger import Actor, Direction, TradeRecord
from updown.orchestration.walkforward.order_mapping import (
    FORMAL_MOVE,
    SENTINEL_RR,
    has_real_target,
    take_profit_orders,
)

COIN = Instrument(
    market=Market.GATE,
    symbol="ZEC_USDT",
    name="지캐시",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
NOW = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
ENTRY = Decimal(100)
FAR_SHORT = Decimal("0.05")  # 숏 다리가 적는 형식 목표 = 진입가의 5%


def short(stop_pct: str, target: Decimal) -> TradeRecord:
    """숏 하나 — 진입 100 · 손절은 위로 `stop_pct`% · 반익과 목표는 같은 값."""
    return TradeRecord(
        trade_id="t823abcd",
        playbook="triangle@0.1",
        actor=Actor.SYSTEM,
        placed_at=NOW,
        entry=ENTRY,
        planned_stop=ENTRY * (1 + Decimal(stop_pct) / 100),
        planned_first=target,
        planned_target=target,
        direction=Direction.SHORT,
    )


class TestShortFormalTarget:
    """성질 ① — 진입가의 5% 값은 손절이 넓어도 형식 목표다."""

    @pytest.mark.parametrize("stop_pct", ["1", "1.9", "2", "4.34", "6.41", "8.93", "9.9"])
    def test_far_short_target_is_formal_whatever_the_stop(self, stop_pct: str) -> None:
        record = short(stop_pct, ENTRY * FAR_SHORT)
        assert not has_real_target(record)
        assert take_profit_orders(record, COIN, 100) == []

    def test_wide_stop_was_the_gap(self) -> None:
        """손절 8.93% 면 리스크 배수는 약 10.6 — 배수 문턱만으로는 못 걸렀다."""
        record = short("8.93", ENTRY * FAR_SHORT)
        multiple = (record.entry - record.planned_target) / (record.planned_stop - record.entry)
        assert multiple < SENTINEL_RR
        assert not has_real_target(record)


class TestRealTargetsStay:
    """성질 ② — 진짜 목표는 그대로다."""

    @pytest.mark.parametrize(
        ("stop_pct", "rr"),
        [("1", "2"), ("4", "2"), ("9.92", "2"), ("3", "2.5"), ("9.92", "2.5")],
    )
    def test_short_with_a_reachable_target_is_real(self, stop_pct: str, rr: str) -> None:
        risk = ENTRY * Decimal(stop_pct) / 100
        record = short(stop_pct, ENTRY - risk * Decimal(rr))
        assert has_real_target(record)
        legs = take_profit_orders(record, COIN, 100)
        assert legs
        assert legs[-1].price == record.planned_target

    def test_boundary_is_half_the_entry_price(self) -> None:
        """딱 절반이면 형식 · 바로 안쪽이면 진짜(리스크 배수는 문턱 아래로 둔다)."""
        at = short("5", ENTRY * (1 - FORMAL_MOVE))
        inside = short("5", ENTRY * (1 - FORMAL_MOVE) + Decimal("0.01"))
        assert not has_real_target(at)
        assert has_real_target(inside)

    def test_long_with_a_reachable_target_is_real(self) -> None:
        record = replace(
            short("5", ENTRY),
            direction=Direction.LONG,
            planned_stop=Decimal(95),
            planned_first=Decimal(105),
            planned_target=Decimal(110),
        )
        assert has_real_target(record)


class TestLongRideStaysFormal:
    """성질 ③ — 롱 타기 다리(리스크 100 배)는 전과 같다."""

    @pytest.mark.parametrize("stop_pct", ["0.3", "1", "5"])
    def test_ride_target_is_formal(self, stop_pct: str) -> None:
        risk = ENTRY * Decimal(stop_pct) / 100
        far = ENTRY + risk * 100
        record = replace(
            short("5", ENTRY),
            direction=Direction.LONG,
            planned_stop=ENTRY - risk,
            planned_first=far,
            planned_target=far,
        )
        assert not has_real_target(record)
        assert take_profit_orders(record, COIN, 100) == []

    def test_missing_target_is_not_real(self) -> None:
        assert not has_real_target(short("5", Decimal(0)))
