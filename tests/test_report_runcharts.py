"""판별 차트 재료 변환 — 원장 기록 → ChartTrade · 제목줄 수치 (T55).

막아야 하는 실패:
1. 🔴 안 채워진 계획(체결 시각 없음)을 그리면 유령 매매가 차트에 뜬다 → None 이어야.
2. 🔴 익절/손절 횟수를 결말 문자열로 세는데 분류가 틀리면 색·수치가 거짓말한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.orchestration.report.performance import Window
from updown.orchestration.report.runcharts import meta_for, to_chart_trade
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

START = datetime(2026, 8, 23, tzinfo=UTC)
WINDOW = Window(since=START, until=START + timedelta(days=1))


def _rec(
    *,
    direction: Direction = Direction.LONG,
    outcome: Outcome = Outcome.TAKE_PROFIT,
    opened: bool = True,
    closed: bool = True,
    first: Decimal = Decimal("108"),
    target: Decimal = Decimal("112"),
) -> TradeRecord:
    return TradeRecord(
        trade_id="t",
        playbook="box@0",
        actor=Actor.SYSTEM,
        direction=direction,
        placed_at=START,
        opened_at=START + timedelta(hours=1) if opened else None,
        entry=Decimal("100"),
        planned_stop=Decimal("96") if direction is Direction.LONG else Decimal("104"),
        planned_first=first,
        planned_target=target,
        outcome=outcome,
        closed_at=START + timedelta(hours=3) if closed else None,
        cost_pct=Decimal("0.0015"),
    )


class TestToChartTrade:
    def test_maps_direction_and_outcome_as_korean(self) -> None:
        ct = to_chart_trade(_rec(direction=Direction.SHORT, outcome=Outcome.STOP_LOSS))
        assert ct is not None
        assert ct.direction == "숏"
        assert ct.outcome == "손절"

    def test_unfilled_plan_is_none(self) -> None:
        # 🔴 체결 시각이 없으면 그릴 자리가 없다.
        assert to_chart_trade(_rec(opened=False)) is None

    def test_first_equal_target_collapses_to_none(self) -> None:
        # 1차 익절이 목표와 같으면 다리가 하나 — 선을 겹쳐 긋지 않는다.
        ct = to_chart_trade(_rec(first=Decimal("112"), target=Decimal("112")))
        assert ct is not None and ct.first is None


class TestMetaFor:
    def test_counts_takes_and_stops_by_outcome(self) -> None:
        records = [
            _rec(outcome=Outcome.TAKE_PROFIT),
            _rec(outcome=Outcome.SIGNAL_EXIT),  # "전환 익절" → 익절
            _rec(outcome=Outcome.STOP_LOSS),
            _rec(outcome=Outcome.LIQUIDATED),  # 강제청산 → 손절 계열
            _rec(outcome=Outcome.LEVEL_EXIT),  # 어느 쪽도 아님
        ]
        meta = meta_for(
            symbol="BTC_USDT",
            playbook="박스",
            window=WINDOW,
            timeframe="15m",
            records=records,
            equity_before=Decimal("1000"),
        )
        assert meta.takes == 2
        assert meta.stops == 2
        assert meta.symbol == "BTC_USDT" and "15m" in meta.period_label

    def test_no_closed_trades_gain_is_none(self) -> None:
        meta = meta_for(
            symbol="BTC_USDT",
            playbook="박스",
            window=WINDOW,
            timeframe="15m",
            records=[_rec(closed=False)],
            equity_before=None,
        )
        assert meta.gain_pct is None and meta.takes == 0
