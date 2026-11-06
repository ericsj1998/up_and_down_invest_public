"""가벼운 첫 화면 재료 — 입금은 손익이 아니고, 못 읽은 칸은 0 으로 꾸미지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from updown.orchestration.report.lite import lite_payload

AT = datetime(2026, 11, 6, 12, tzinfo=UTC)
MOVES = [{"change": "300"}, {"change": "408"}, {"change": "-50"}]


def make(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "at": AT,
        "available": Decimal(500),
        "positions": [],
        "capital_moves": MOVES,
        "realized_24h": Decimal("-3"),
        "runs": 58,
        "leader": True,
    }
    return lite_payload(**(base | kw))  # type: ignore[arg-type]


def test_deposit_is_not_earnings() -> None:
    made = make()
    assert made["capital"] == "658"
    assert made["earned"] == "-158"


def test_position_margin_counts_in_total_and_flat_rows_are_dropped() -> None:
    made = make(
        positions=[
            {"symbol": "BTC_USDT", "size": "-3", "margin": "100", "unrealised_pnl": "-5"},
            {"symbol": "ETH_USDT", "size": "2", "margin": "50", "unrealised_pnl": "10"},
            {"symbol": "SOL_USDT", "size": "0", "margin": "9", "unrealised_pnl": "9"},
        ]
    )
    assert made["total"] == "650"
    assert made["unrealized"] == "5"
    rows = made["positions"]
    assert isinstance(rows, list)
    assert [(r["symbol"], r["side"]) for r in rows] == [("BTC_USDT", "short"), ("ETH_USDT", "long")]
    assert Decimal(rows[0]["pct"]) == Decimal(-5)


def test_unread_capital_leaves_earnings_blank() -> None:
    made = make(capital_moves=None)
    assert made["capital"] is None
    assert made["earned"] is None
    assert made["total"] == "500"


def test_unread_positions_leave_total_blank() -> None:
    made = make(positions=None)
    assert made["total"] is None
    assert made["unrealized"] is None
    assert made["earned"] is None
    assert made["positions"] == []
