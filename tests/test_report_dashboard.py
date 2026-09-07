"""리포트 대시보드 JSON — 메일과 같은 숫자 · 못 읽은 값은 None (T220 2단계)."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from updown.orchestration.report import dashboard as dash
from updown.orchestration.report.html import AccountSummary
from updown.orchestration.report.performance import (
    ExchangeSummary,
    LedgerSummary,
    Performance,
    Window,
)


class _Rec:
    """`gain_curve` 가 읽는 칸만 있는 기록."""

    def __init__(
        self, closed_at: datetime | None, gain_pct: Decimal | None, symbol: str = "BTC_USDT"
    ) -> None:
        self.closed_at = closed_at
        self.gain_pct = gain_pct
        self.symbol = symbol


T0 = datetime(2026, 9, 5, tzinfo=UTC)


def test_gain_curve_sorts_by_close_and_accumulates() -> None:
    records: list[tuple[str, Any]] = [
        ("run-a", _Rec(T0 + timedelta(hours=2), Decimal("-1.5"), "ETH_USDT")),
        ("run-b", _Rec(T0 + timedelta(hours=1), Decimal("2.0"))),
        ("run-c", _Rec(None, Decimal("9.9"))),  # 보유 중 — 곡선에 안 들어간다
        ("run-d", _Rec(T0 + timedelta(hours=3), None)),  # 손익률 없음 — 지어내지 않는다
    ]
    curve = dash.gain_curve(records, {"run-a": "ETH_USDT", "run-b": "BTC_USDT"})
    assert [p.symbol for p in curve] == ["BTC_USDT", "ETH_USDT"]
    assert [p.cum_pct for p in curve] == [Decimal("2.0"), Decimal("0.5")]


def test_payload_serializes_decimals_as_strings_and_keeps_none() -> None:
    window = Window(since=T0, until=T0 + timedelta(hours=24))
    ledger = LedgerSummary(
        trades=3,
        longs=2,
        shorts=1,
        wins=2,
        gain_sum_pct=Decimal("4.25"),
        mean_rr=None,
        max_drawdown_pct=Decimal("1.10"),
        by_outcome={"take": 2, "stop": 1},
        by_playbook={"pb": 3},
        by_symbol={"BTC_USDT": 3},
    )
    perf = Performance(
        window=window,
        ledger=ledger,
        exchange=ExchangeSummary(
            pnl=Decimal("12.3"), fees=Decimal("-0.8"), funding=Decimal("0"), rows=5
        ),
        diverged=False,
        note="맞다",
    )
    account = AccountSummary(
        total=Decimal("300"),
        available=Decimal("291.88"),
        locked=Decimal(0),
        realized=Decimal("12.3"),
        unrealized=None,
        vault=None,
        win_rate=ledger.win_rate,
        trades=3,
        runs=6,
        diverged=False,
        note="맞다",
    )
    rows = [dash.RunRow("live1", "BTC_USDT", "pb@1", "fund-a", Decimal("1.5"), 1, 0, 1)]
    curve = [
        dash.CurvePoint(at=T0, symbol="BTC_USDT", gain_pct=Decimal("1.5"), cum_pct=Decimal("1.5"))
    ]

    out = dash.payload(perf, account, rows, curve, configured=True, default_to=["a@b.c"])

    assert out["ledger"]["gain_sum_pct"] == "4.25" and out["ledger"]["win_rate"] is not None
    assert out["ledger"]["mean_rr"] is None  # 없는 값은 None — 0 이 아니다
    assert out["exchange"]["pnl"] == "12.3" and out["exchange"]["rows"] == 5
    assert out["account"]["unrealized"] is None and out["account"]["total"] == "300"
    assert out["runs"][0] == {
        "key": "live1",
        "symbol": "BTC_USDT",
        "playbook": "pb@1",
        "fund": "fund-a",
        "gain_pct": "1.5",
        "takes": 1,
        "stops": 0,
        "trades": 1,
    }
    assert out["curve"][0]["cum_pct"] == "1.5" and out["configured"] is True


def test_payload_without_exchange_or_account() -> None:
    window = Window(since=T0, until=T0 + timedelta(hours=1))
    perf = Performance(
        window=window, ledger=LedgerSummary(), exchange=None, diverged=False, note=""
    )
    out = dash.payload(perf, None, [], [], configured=False, default_to=[])
    assert (
        out["exchange"] is None
        and out["account"] is None
        and out["runs"] == []
        and out["curve"] == []
    )
    # 대시보드가 기대하는 칸이 전부 있다 — 화면 타입(api.ts)과 맞춰 둔다
    assert set(out) == {
        "window",
        "ledger",
        "exchange",
        "diverged",
        "note",
        "account",
        "funds",
        "runs",
        "curve",
        "equity_monthly",
        "configured",
        "default_to",
    }
    assert {f.name for f in fields(dash.RunRow)} == {
        "key",
        "symbol",
        "playbook",
        "fund_label",
        "gain_pct",
        "takes",
        "stops",
        "trades",
    }
