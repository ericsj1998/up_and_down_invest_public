"""가벼운 첫 화면 재료 — 입금은 손익이 아니고, 못 읽은 칸은 0 으로 꾸미지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.apps.api.rebalancer import _held_units, engine_note, engine_row
from updown.orchestration.report.lite import brief_payload, lite_payload
from updown.orchestration.report.performance import (
    ExchangeSummary,
    LedgerSummary,
    Performance,
    Window,
)

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


def test_position_carries_its_run_and_engine_leg_has_none() -> None:
    made = make(
        positions=[
            {"symbol": "NEAR_USDT", "size": "12", "margin": "11", "unrealised_pnl": "-1"},
            {"symbol": "NAS100_USDT", "size": "29", "margin": "26", "unrealised_pnl": "1"},
        ],
        run_of={"NEAR_USDT": "abc"},
    )
    rows = {r["symbol"]: r["run"] for r in made["positions"]}  # type: ignore[union-attr]
    assert rows == {"NEAR_USDT": "abc", "NAS100_USDT": ""}


def test_position_shows_money_at_work_leg_and_exchange_stops() -> None:
    made = make(
        positions=[
            {
                "symbol": "NEAR_USDT",
                "size": "12",
                "margin": "11.3",
                "unrealised_pnl": "-1",
                "value": "45.05",
                "entry_price": "3.7538",
                "mark_price": "3.7",
            },
            {
                "symbol": "XRP_USDT",
                "size": "-4",
                "margin": "20",
                "unrealised_pnl": "2",
                "value": "-80",
            },
        ],
        shares_of={"NEAR_USDT": [{"name": "다리 A", "stop": "3.3895", "opened_at": None}]},
        stops=[{"symbol": "NEAR_USDT"}, {"symbol": "NEAR_USDT"}],
        halted=True,
    )
    assert made["margin_used"] == "31.3"
    assert made["available"] == "500"
    assert made["notional"] == "125.05"
    assert Decimal(str(made["account_leverage"])) == Decimal("125.05") / Decimal("531.3")
    assert made["halted"] is True
    rows = {r["symbol"]: r for r in made["positions"]}  # type: ignore[union-attr]
    near = rows["NEAR_USDT"]
    assert (near["contracts"], near["margin"], near["notional"]) == ("12", "11.3", "45.05")
    assert (near["entry"], near["mark"]) == ("3.7538", "3.7")
    assert near["shares"][0]["name"] == "다리 A"
    assert near["exchange_stops"] == 2
    assert rows["XRP_USDT"]["notional"] == "80"
    assert rows["XRP_USDT"]["contracts"] == "4"
    assert rows["XRP_USDT"]["shares"] == []
    assert rows["XRP_USDT"]["exchange_stops"] == 0


def test_engine_units_include_the_filled_buy_and_skip_ghosts() -> None:
    """나스닥 줄 · 카드가 낮에 찬 계약도 몫으로 보여 준다(다음 마감 전에도)."""
    got = _held_units(
        {
            "units": [
                {"key": "c1", "name": "계단", "contracts": 3, "ghost": False, "fill": "1"},
                {"key": "c2", "name": "계단", "contracts": 0, "ghost": True, "fill": "1"},
            ],
            "buy": {
                "slot": 2,
                "name": "계단",
                "filled": 29,
                "fill": "27132",
                "stop": "18993",
                "filled_at": "2026-11-06T21:01:30+00:00",
            },
        }
    )
    assert [(u["key"], u["contracts"]) for u in got] == [("c1", 3), ("계단 2", 29)]
    assert got[1]["fill"] == "27132" and got[1]["stop"] == "18993"
    row = engine_row(
        {
            "units": [],
            "buy": {"slot": 2, "name": "계단", "filled": 29, "fill": "27132", "stop": "18993"},
        },
        "1",
        "다리",
        "pb",
    )
    assert row["holding"] is True
    assert row["shares"][0]["name"] == "다리 계단"
    assert row["position"]["stop"] == "18993"


def test_engine_note_rides_only_on_its_own_position_row() -> None:
    """엔진 다리 요약은 그 종목 줄에만 붙는다 — 다른 줄에 빈 칸을 만들지 않는다."""
    note = engine_note(
        {
            "step_units": 2,
            "max_units": 5,
            "buy": {"slot": 3, "level": "25480", "contracts": 4, "filled": 0},
            "next_close": "2026-11-10T21:00:00+00:00",
        }
    )
    assert note == {
        "step_units": 2,
        "max_units": 5,
        "next_step": "25480",
        "next_close": "2026-11-10T21:00:00+00:00",
    }
    made = make(
        positions=[
            {"symbol": "NEAR_USDT", "size": "12", "margin": "11", "unrealised_pnl": "-1"},
            {"symbol": "NAS100_USDT", "size": "29", "margin": "26", "unrealised_pnl": "1"},
        ],
        engine_of={"NAS100_USDT": note},
    )
    rows = {r["symbol"]: r for r in made["positions"]}  # type: ignore[union-attr]
    assert rows["NAS100_USDT"]["engine"] == note
    assert "engine" not in rows["NEAR_USDT"]


def test_engine_note_has_no_next_step_once_the_order_is_full_or_absent() -> None:
    full = engine_note({"buy": {"slot": 3, "level": "25480", "contracts": 4, "filled": 4}})
    assert full["next_step"] is None
    assert engine_note({})["next_step"] is None


def test_engine_row_passes_each_share_exit_through() -> None:
    row = engine_row(
        {
            "units": [
                {
                    "key": "u1",
                    "name": "몫",
                    "contracts": 3,
                    "fill": "100",
                    "stop": "70",
                    "ghost": False,
                    "opened": "t1",
                    "exit": "104",
                    "exit_how": "limit",
                },
                {
                    "key": "u2",
                    "name": "몫",
                    "contracts": 3,
                    "fill": "98",
                    "stop": "68.6",
                    "ghost": False,
                    "opened": "t2",
                },
            ],
        },
        "1",
        "다리",
        "pb",
    )
    assert [(s["exit"], s["exit_how"]) for s in row["shares"]] == [("104", "limit"), (None, "")]


def test_unread_stops_are_not_counted_as_no_stops() -> None:
    made = make(positions=[{"symbol": "A", "size": "1", "margin": "1", "unrealised_pnl": "0"}])
    assert made["positions"][0]["exchange_stops"] is None  # type: ignore[index]
    assert made["positions"][0]["notional"] is None  # type: ignore[index]


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


def perf(*, hours: int, exchange: ExchangeSummary | None) -> Performance:
    return Performance(
        window=Window(since=AT - timedelta(hours=hours), until=AT),
        ledger=LedgerSummary(trades=4, longs=3, shorts=1, wins=2),
        exchange=exchange,
        diverged=False,
        note="",
    )


def test_brief_net_is_after_fees_and_funding() -> None:
    made = brief_payload(
        perf(
            hours=168,
            exchange=ExchangeSummary(
                pnl=Decimal(10), fees=Decimal("-2"), funding=Decimal("-1"), rows=5
            ),
        ),
        168,
    )
    assert made["net"] == "7"
    assert made["trades"] == 4
    assert Decimal(made["win_rate"]) == Decimal(50)
    assert made["clipped"] is False


def test_brief_marks_window_shortened_by_account_start() -> None:
    assert brief_payload(perf(hours=2, exchange=None), 720)["clipped"] is True


def test_brief_unread_exchange_is_blank_not_zero() -> None:
    made = brief_payload(perf(hours=24, exchange=None), 24)
    assert made["net"] is None
    assert made["fees"] is None
