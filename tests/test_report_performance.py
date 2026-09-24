"""이메일 리포트 집계 (T35) — 원장과 거래소를 나란히, 갈리면 갈렸다고.

순수 함수만 시험한다 — DB·SMTP 는 안 건드린다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.orchestration.report.performance import (
    ExchangeSummary,
    LedgerSummary,
    Performance,
    Window,
    book_reaches,
    compare,
    kst_day_and_month,
    render_text,
    summarize_account_book,
    summarize_records,
)
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

T0 = datetime(2026, 8, 22, 0, 0, tzinfo=UTC)
WINDOW = Window(since=T0, until=T0 + timedelta(hours=24))


def _trade(
    *,
    closed_hours: float,
    entry: str,
    exit_price: str,
    short: bool = False,
    outcome: Outcome = Outcome.TAKE_PROFIT,
    playbook: str = "box@0.66",
) -> TradeRecord:
    price = Decimal(entry)
    return TradeRecord(
        trade_id=f"t{closed_hours}{entry}",
        playbook=playbook,
        actor=Actor.SYSTEM,
        placed_at=T0,
        entry=price,
        opened_at=T0,
        planned_stop=price + 5 if short else price - 5,
        planned_target=price - 20 if short else price + 20,
        planned_first=price - 10 if short else price + 10,
        direction=Direction.SHORT if short else Direction.LONG,
        outcome=outcome,
        closed_at=T0 + timedelta(hours=closed_hours),
        exit_price=Decimal(exit_price),
        cost_pct=Decimal(0),
    )


class TestLedgerSummary:
    def test_only_trades_closed_inside_the_window_count(self) -> None:
        """청산 시각 기준 — 구간 밖(전날·다음날)은 뺀다."""
        rows = [
            ("BTC", _trade(closed_hours=1, entry="100", exit_price="110")),
            ("BTC", _trade(closed_hours=-1, entry="100", exit_price="110")),
            ("BTC", _trade(closed_hours=25, entry="100", exit_price="110")),
        ]
        assert summarize_records(rows, WINDOW).trades == 1

    def test_direction_split_and_win_rate(self) -> None:
        """92% 숏 같은 편향이 메일에서 보여야 한다 — 롱/숏을 따로 센다."""
        rows = [
            ("BTC", _trade(closed_hours=1, entry="100", exit_price="110")),
            (
                "ETH",
                _trade(closed_hours=2, entry="100", exit_price="95", outcome=Outcome.STOP_LOSS),
            ),
            ("ETH", _trade(closed_hours=3, entry="100", exit_price="90", short=True)),
        ]
        found = summarize_records(rows, WINDOW)
        assert (found.longs, found.shorts) == (2, 1)
        assert found.wins == 2
        assert found.win_rate is not None and round(found.win_rate, 1) == Decimal("66.7")
        assert found.by_symbol == {"BTC": 1, "ETH": 2}

    def test_cancelled_orders_are_not_trades(self) -> None:
        rows = [
            (
                "BTC",
                _trade(closed_hours=1, entry="100", exit_price="100", outcome=Outcome.CANCELLED),
            )
        ]
        assert summarize_records(rows, WINDOW).trades == 0

    def test_max_drawdown_follows_the_cumulative_path(self) -> None:
        """+10 → -5 → -5 → +10 이면 정점 10 에서 0 까지 = 낙폭 10%p."""
        rows = [
            ("BTC", _trade(closed_hours=1, entry="100", exit_price="110")),
            (
                "BTC",
                _trade(closed_hours=2, entry="100", exit_price="95", outcome=Outcome.STOP_LOSS),
            ),
            (
                "BTC",
                _trade(closed_hours=3, entry="100", exit_price="95", outcome=Outcome.STOP_LOSS),
            ),
            ("BTC", _trade(closed_hours=4, entry="100", exit_price="110")),
        ]
        found = summarize_records(rows, WINDOW)
        assert found.max_drawdown_pct == Decimal(10)
        assert found.gain_sum_pct == Decimal(10)


class TestExchangeSummary:
    def test_pnl_fee_and_funding_are_kept_apart(self) -> None:
        """⚠️ 비용을 따로 낸다 — 손실의 80% 가 수수료였던 적이 있다."""
        stamp = str(int((T0 + timedelta(hours=1)).timestamp()))
        rows = [
            {"type": "pnl", "change": "12.5", "time": stamp},
            {"type": "fee", "change": "-0.4", "time": stamp},
            {"type": "fund", "change": "-0.1", "time": stamp},
            {"type": "dnw", "change": "1000", "time": stamp},
        ]
        found = summarize_account_book(rows, WINDOW)
        assert (found.pnl, found.fees, found.funding) == (
            Decimal("12.5"),
            Decimal("-0.4"),
            Decimal("-0.1"),
        )
        assert found.net == Decimal("12.0")
        assert found.other == {"dnw": 1}

    def test_rows_outside_the_window_are_ignored_and_unreadable_rows_are_counted(self) -> None:
        late = str(int((T0 + timedelta(hours=30)).timestamp()))
        rows = [
            {"type": "pnl", "change": "5", "time": late},
            {"type": "pnl", "change": "x", "time": "?"},
        ]
        found = summarize_account_book(rows, WINDOW)
        assert found.rows == 0
        assert found.other == {"unreadable": 1}


class TestCompare:
    def test_opposite_signs_are_a_divergence(self) -> None:
        """🔴 한쪽이 벌었다는데 다른 쪽이 잃었으면 둘 중 하나는 틀렸다."""
        ledger = LedgerSummary(trades=3, gain_sum_pct=Decimal(5))
        exchange = ExchangeSummary(pnl=Decimal(-3), rows=4, pnl_rows=2)
        diverged, note = compare(ledger, exchange)
        assert diverged and "부호" in note

    def test_fee_and_funding_only_days_are_not_a_divergence(self) -> None:
        """들고만 있어도 거래소엔 수수료·펀딩 행이 생긴다 — 마감이 없다고 갈린 게 아니다."""
        exchange = ExchangeSummary(fees=Decimal("-0.03"), funding=Decimal("-0.008"), rows=12)
        diverged, note = compare(LedgerSummary(), exchange)
        assert not diverged and "청산이 없다" in note

    def test_exchange_only_realized_points_at_resize(self) -> None:
        """재레버 감축은 거래소에만 실현을 남긴다 — 갈렸다고 쓰되 이유가 붙는다."""
        exchange = ExchangeSummary(pnl=Decimal("-0.24"), rows=12, pnl_rows=1)
        diverged, note = compare(LedgerSummary(), exchange)
        assert diverged and "재레버" in note

    def test_one_sided_records_are_a_divergence(self) -> None:
        diverged, _ = compare(
            LedgerSummary(trades=2, gain_sum_pct=Decimal(1)), ExchangeSummary(rows=0)
        )
        assert diverged

    def test_missing_exchange_is_not_silently_fine(self) -> None:
        diverged, note = compare(LedgerSummary(), None)
        assert not diverged and "못 읽었다" in note


def test_render_carries_both_sides_and_the_verdict() -> None:
    perf = Performance(
        window=WINDOW,
        ledger=LedgerSummary(trades=1, longs=1, wins=1, gain_sum_pct=Decimal("2.5")),
        exchange=ExchangeSummary(pnl=Decimal("3"), fees=Decimal("-0.2"), rows=2),
        diverged=False,
        note="원장과 거래소의 손익 부호가 같다",
    )
    text = render_text(perf)
    assert "[원장" in text and "[거래소" in text and "✅ 일치" in text
    assert "수수료 -0.2000" in text


def test_window_rejects_naive_and_empty() -> None:
    with pytest.raises(ValueError, match="UTC"):
        Window(since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))
    with pytest.raises(ValueError, match="비었다"):
        Window(since=T0, until=T0)


class TestConsolePnlWindows:
    """콘솔 손익 카드 — 이번 달(큰 값) · 오늘(작은 줄) 구간과 장부가 닿았나 (사용자 2026-09-24)."""

    def test_kst_day_and_month_anchor_on_kst_midnight(self) -> None:
        # UTC 9월 30일 16:30 = KST 10월 1일 01:30
        # → 오늘도 이번 달도 KST 10월 1일 00시(= UTC 9월 30일 15:00)에서 시작
        now = datetime(2026, 9, 30, 16, 30, tzinfo=UTC)
        day, month = kst_day_and_month(now)
        assert day.since == datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
        assert month.since == day.since
        # UTC 9월 24일 03:00 = KST 12:00 — 오늘은 UTC 23일 15:00, 이번 달은 UTC 8월 31일 15:00
        day, month = kst_day_and_month(datetime(2026, 9, 24, 3, 0, tzinfo=UTC))
        assert day.since == datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
        assert month.since == datetime(2026, 8, 31, 15, 0, tzinfo=UTC)
        assert day.until > datetime(2026, 9, 24, 3, 0, tzinfo=UTC)

    def test_kst_midnight_exactly_is_not_an_empty_window(self) -> None:
        day, _ = kst_day_and_month(datetime(2026, 9, 23, 15, 0, tzinfo=UTC))
        assert day.since < day.until

    def test_kst_day_and_month_rejects_naive(self) -> None:
        with pytest.raises(ValueError, match="UTC"):
            kst_day_and_month(datetime(2026, 9, 24, 3, 0))

    def test_month_sum_counts_only_since_month_start(self) -> None:
        _, month = kst_day_and_month(datetime(2026, 9, 24, 3, 0, tzinfo=UTC))
        before = datetime(2026, 8, 31, 14, 0, tzinfo=UTC).timestamp()  # KST 8월 31일 23시 — 지난달
        inside = datetime(2026, 9, 2, 0, 0, tzinfo=UTC).timestamp()
        rows = [
            {"type": "pnl", "change": "10", "time": str(before)},
            {"type": "pnl", "change": "5", "time": str(inside)},
            {"type": "fee", "change": "-0.5", "time": str(inside)},
        ]
        assert summarize_account_book(rows, month).net == Decimal("4.5")

    def test_book_reaches_when_short_or_old_enough(self) -> None:
        since = datetime(2026, 9, 1, tzinfo=UTC)

        def row(when: datetime, *, ms: bool = False) -> dict[str, str]:
            stamp = when.timestamp()
            return {"type": "pnl", "change": "1", "time": str(int(stamp * 1000) if ms else stamp)}

        new = row(datetime(2026, 9, 20, tzinfo=UTC))
        old = row(datetime(2026, 8, 20, tzinfo=UTC))
        # 요청보다 적게 왔다 = 있는 것을 다 받았다
        assert book_reaches([new], since, limit=3)
        # 꽉 찼지만 가장 오래된 줄이 월초 전
        assert book_reaches([new, new, old], since, limit=3)
        # 꽉 찼고 월초에 못 닿았다 → 칸을 비운다
        assert not book_reaches([new, new, new], since, limit=3)
        # 바이낸스 ms 도 같은 자
        old_ms = row(datetime(2026, 8, 1, tzinfo=UTC), ms=True)
        assert book_reaches([new, new, old_ms], since, limit=3)
