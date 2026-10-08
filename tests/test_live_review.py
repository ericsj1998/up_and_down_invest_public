"""라이브 매매 분석기(T444) — 순수 계산 시험.

## 무엇을 막으려는 시험인가

1. 거래소 닫힌 포지션 기록을 **엉뚱한 매매에** 붙이는 것 — 매매 id 앞자리 + 종목 + 시각 창 셋 다
   맞아야 한다.
2. 같은 봉에 익절 · 손절이 둘 다 닿았을 때 익절로 세는 것(낙관) — 손절 먼저.
3. 숏 매매의 R 부호가 뒤집히는 것.
4. 손실 몫의 합이 100% 가 안 되는 것.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from updown.orchestration.live_review.attribution import (
    leg_table,
    money_of,
    outside_money,
    trade_prefix_of_text,
)
from updown.orchestration.live_review.excursion import Bar, excursion, grid_outcome
from updown.orchestration.live_review.health import funnel_totals, playbook_spans, summarize_events
from updown.orchestration.live_review.snapshot import Run, Trade

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _trade(
    *,
    trade_id: str = "abcdef12-rest",
    symbol: str = "BTC_USDT",
    leg: str = "private_strategy",
    direction: str = "LONG",
    entry: str = "100",
    stop: str = "98",
    exit_price: str | None = "103",
    opened: datetime = T0,
    closed: datetime | None = T0 + timedelta(hours=3),
    margin: str = "50",
    lev: str = "4",
) -> Trade:
    return Trade(
        run_id="run",
        symbol=symbol,
        run_playbook_id="private_strategy",
        trade_id=trade_id,
        playbook=f"{leg}@0.1.0",
        direction=direction,
        outcome="익절" if exit_price else "",
        actor="시스템",
        placed_at=opened,
        opened_at=opened,
        closed_at=closed,
        entry=Decimal(entry),
        exit_price=None if exit_price is None else Decimal(exit_price),
        planned_stop=Decimal(stop),
        planned_target=None,
        leverage=Decimal(lev),
        filled_leverage=None,
        margin_used=Decimal(margin),
        contracts=10,
        funding_paid=Decimal(0),
        fee_actual=Decimal("0.5"),
        realized_adjust=None,
        half_price=None,
        half_at=None,
    )


class TestMoney:
    def test_exchange_close_matches_by_prefix_symbol_and_time(self) -> None:
        t = _trade()
        closes = [
            {
                "contract": "BTC_USDT",
                "time": str((T0 + timedelta(hours=3)).timestamp()),
                "pnl": "11.5",
                "text": "t-13dcac-abcdef12-cl",
            },
            {
                "contract": "ETH_USDT",
                "time": str((T0 + timedelta(hours=3)).timestamp()),
                "pnl": "-99",
                "text": "t-13dcac-abcdef12-cl",
            },
            {
                "contract": "BTC_USDT",
                "time": str((T0 + timedelta(days=5)).timestamp()),
                "pnl": "-50",
                "text": "t-13dcac-abcdef12-cl",
            },
        ]
        got = money_of(t, closes)
        assert got.source == "exchange"
        assert got.pnl == Decimal("11.5")
        assert got.r == Decimal("1.5")  # (103 - 100) ÷ (100 - 98)

    def test_ledger_estimate_when_no_exchange_row(self) -> None:
        got = money_of(_trade(), [])
        assert got.source == "ledger"
        # 3% x 50 x 4 = 6 - 수수료 0.5
        assert got.pnl == Decimal("5.5")

    def test_open_trade_has_no_money(self) -> None:
        got = money_of(_trade(exit_price=None, closed=None), [])
        assert got.source == "open" and got.pnl is None

    def test_short_r_sign(self) -> None:
        t = _trade(direction="SHORT", entry="100", stop="102", exit_price="97")
        assert money_of(t, []).r == Decimal("1.5")

    def test_prefix_of_text(self) -> None:
        assert trade_prefix_of_text("t-13dcac-832fa68b-cl-1") == "832fa68b"
        assert trade_prefix_of_text("t-72bb3b4690f3-cl-0") == "72bb3b4690f3"
        assert trade_prefix_of_text("ao-2108200708237") == ""


class TestLegTable:
    def test_loss_share_sums_to_100(self) -> None:
        rows = [
            money_of(_trade(trade_id="a1", leg="L1", exit_price="97"), []),  # -3% x 200 = -6 -0.5
            money_of(_trade(trade_id="a2", leg="L2", exit_price="99"), []),  # -1% x 200 = -2 -0.5
            money_of(_trade(trade_id="a3", leg="L2", exit_price="104"), []),  # +8 -0.5
        ]
        table = leg_table(rows)
        shares = [r.loss_share_pct for r in table if r.loss_share_pct is not None]
        assert abs(sum(shares, Decimal(0)) - Decimal(100)) < Decimal("0.001")
        worst = table[0]
        assert worst.leg == "L1" and worst.losses == 1 and worst.wins == 0

    def test_outside_money_counts_only_human_orders(self) -> None:
        book = [
            {"type": "pnl", "change": "-37.4", "text": "BTC_USDT:2"},
            {"type": "fee", "change": "-2.1", "text": "BTC_USDT:3"},
            {"type": "pnl", "change": "-10", "text": "TRB_USDT:1"},
            {"type": "pnl", "change": "-5", "text": "ETH_USDT:9"},  # 모름 — 안 센다
        ]
        texts = {"1": "t-13dcac-832fa68", "2": "web_p_1", "3": "ao-1"}
        assert outside_money(book, texts) == Decimal("-39.5")


def _bars(
    prices: list[tuple[str, str, str, str]], start: datetime = T0 - timedelta(minutes=30)
) -> list[Bar]:
    return [
        Bar(start + timedelta(minutes=5 * i), Decimal(o), Decimal(h), Decimal(lo), Decimal(c))
        for i, (o, h, lo, c) in enumerate(prices)
    ]


class TestExcursion:
    def test_mfe_mae_and_after_exit(self) -> None:
        t = _trade(
            entry="100", stop="98", exit_price="101", opened=T0, closed=T0 + timedelta(minutes=10)
        )
        bars = _bars(
            [
                ("99", "99.5", "98.5", "99"),  # 진입 전 6봉(-30분 ~)
                ("99", "99.5", "98.5", "99"),
                ("99", "99.5", "98.5", "99"),
                ("99", "99.5", "98.5", "99"),
                ("99", "99.5", "98.5", "99"),
                ("99", "99.5", "98.5", "99"),
                ("100", "101", "99", "100.5"),  # T0 진입 봉 — MAE 0.5R
                ("100.5", "104", "100", "103"),  # MFE 2R
                ("103", "103", "101", "101"),  # 청산 봉(T0+10)
                ("101", "106", "100.5", "105"),  # 청산 뒤 — 최고 3R
                ("105", "105", "96", "97"),  # 청산 뒤 — 최악 2R
            ]
        )
        ex = excursion(t, bars, after=10)
        assert ex is not None
        assert ex.mfe_r == Decimal(2) and ex.mae_r == Decimal("0.5")
        assert ex.bars_held == 3
        assert ex.after_best_r == Decimal(3) and ex.after_worst_r == Decimal(2)
        assert ex.exit_r == Decimal("0.5")

    def test_same_bar_counts_the_stop_first(self) -> None:
        t = _trade(entry="100", stop="98", opened=T0, closed=T0 + timedelta(minutes=5))
        bars = _bars([("100", "105", "97", "104")], start=T0)
        assert grid_outcome(t, bars, Decimal(2), Decimal(1)) == Decimal(-1)

    def test_grid_hits_target_then_stops_counting(self) -> None:
        t = _trade(entry="100", stop="98", opened=T0, closed=T0 + timedelta(minutes=15))
        bars = _bars(
            [
                ("100", "101", "99.5", "100.5"),
                ("100.5", "103.1", "100.4", "103"),
                ("103", "103", "95", "95"),
            ],
            start=T0,
        )
        assert grid_outcome(t, bars, Decimal("1.5"), Decimal(1)) == Decimal("1.5")
        assert grid_outcome(t, bars, Decimal(3), Decimal(1)) == Decimal(-1)

    def test_short_grid(self) -> None:
        t = _trade(
            direction="SHORT", entry="100", stop="102", opened=T0, closed=T0 + timedelta(minutes=10)
        )
        bars = _bars([("100", "100.5", "99", "99.2"), ("99.2", "99.5", "96.9", "97")], start=T0)
        assert grid_outcome(t, bars, Decimal("1.5"), Decimal(1)) == Decimal("1.5")


class TestHealth:
    def test_funnel_totals_by_bundle(self) -> None:
        runs = [
            Run(
                "1",
                "k1",
                "BTC_USDT",
                "bundle",
                "bundle@2.5.0",
                None,
                None,
                T0,
                None,
                "",
                {"funnel": {"gate:fit": 3, "brake": 1}},
            ),
            Run(
                "2",
                "k2",
                "ETH_USDT",
                "bundle",
                "bundle@2.5.0",
                None,
                None,
                T0,
                None,
                "",
                {"funnel": {"gate:fit": 2}},
            ),
            Run("3", "k3", "XRP_USDT", "other", "other@1", None, None, T0, None, "", {}),
        ]
        got = funnel_totals(runs)
        assert got == {"bundle": {"gate:fit": 5, "brake": 1}}

    def test_event_summary_counts_audit_and_errors(self) -> None:
        events: list[dict[str, Any]] = [
            {
                "event_type": "live_audit_found",
                "level": "warning",
                "ts": T0.isoformat(),
                "payload": {"code": "ledger_mismatch", "symbol": "BTC_USDT"},
            },
            {
                "event_type": "reconcile_finding",
                "level": "error",
                "ts": T0.isoformat(),
                "payload": {"code": "reconcile_orphan_position"},
            },
            {
                "event_type": "session_entry_gate_fit",
                "level": "info",
                "ts": T0.isoformat(),
                "payload": {"by": "brake"},
            },
            {"event_type": "analysis_frame", "level": "info", "ts": T0.isoformat(), "payload": {}},
        ]
        got = summarize_events(events)
        assert got.audit_codes == {"ledger_mismatch": 1}
        assert got.reconcile_codes == {"reconcile_orphan_position": 1}
        assert got.errors == {"live_audit_found": 1, "reconcile_finding": 1}
        assert got.entry_gates == {"session_entry_gate_fit:brake": 1}
        assert "analysis_frame" not in got.by_type

    def test_playbook_spans(self) -> None:
        trades = [
            _trade(trade_id="a", opened=T0),
            _trade(trade_id="b", opened=T0 + timedelta(days=1)),
        ]
        spans = playbook_spans(trades)
        assert len(spans) == 1 and spans[0].trades == 2 and spans[0].last == T0 + timedelta(days=1)


class TestWhyNoEntry:
    def test_candidates_blocked_and_entered_per_leg(self) -> None:
        from updown.orchestration.live_review.health import held_events, why_no_entry

        runs = [
            Run(
                "1",
                "k1",
                "BTC_USDT",
                "bundle",
                "bundle@2.5.0",
                None,
                None,
                T0,
                None,
                "",
                {
                    "funnel": {
                        "cand:private_strategy": 7,
                        "entered:private_strategy@0.1.0": 2,
                        "blocked:private_strategy": 1,
                        "ref_sma": 5,
                        "gate:brake": 2,
                        "bars:TREND": 400,
                    }
                },
            ),
            Run(
                "2",
                "k2",
                "ETH_USDT",
                "bundle",
                "bundle@2.5.0",
                None,
                None,
                T0,
                None,
                "",
                {
                    "funnel": {
                        "cand:private_strategy": 3,
                        "cand:private_strategy": 0,
                        "ref_sma": 1,
                    }
                },
            ),
        ]
        got = why_no_entry(runs)
        rows, shared = got["bundle"]
        by = {r.leg: r for r in rows}
        assert by["private_strategy"].candidates == 10
        assert by["private_strategy"].entered == 2
        assert by["private_strategy"].blocked == 1
        assert (
            by["private_strategy"].candidates == 0
        )  # 탐지기가 안 울림 — 문 탓이 아니다
        assert shared["ref_sma"] == 6 and shared["gate:brake"] == 2
        assert "bars:TREND" not in shared

        events: list[dict[str, Any]] = [
            {
                "event_type": "session_entry_ref_sma_held",
                "ts": T0.isoformat(),
                "payload": {"symbol": "TRB_USDT", "note": "기준 SMA"},
            },
            {
                "event_type": "session_entry_ref_sma_held",
                "ts": (T0 + timedelta(hours=4)).isoformat(),
                "payload": {"symbol": "ZEC_USDT", "note": "기준 SMA"},
            },
            {"event_type": "live_run_resumed", "ts": T0.isoformat(), "payload": {}},
        ]
        held = held_events(events)
        assert len(held) == 1 and held[0].count == 2 and held[0].symbols == 2
        assert held[0].last_at == T0 + timedelta(hours=4)


def test_dedupe_keeps_the_live_row_over_the_transferred_cancel() -> None:
    from updown.orchestration.live_review.snapshot import dedupe_trades

    old = _trade(trade_id="same", exit_price=None, closed=T0 + timedelta(hours=1))
    old = replace(old, outcome="취소")
    new = _trade(trade_id="same", exit_price="103")
    got = dedupe_trades([old, new])
    assert len(got) == 1 and got[0].outcome != "취소"


def test_stale_open_rows_are_marked_when_their_run_is_closed() -> None:
    from updown.orchestration.live_review.attribution import mark_stale_open

    live = money_of(_trade(trade_id="a", exit_price=None, closed=None), [])
    stale = money_of(_trade(trade_id="b", exit_price=None, closed=None), [])
    got = mark_stale_open([live, stale], frozenset({"run"}))  # 두 매매 모두 run_id="run"
    assert [r.source for r in got] == ["stale", "stale"]
    got2 = mark_stale_open([live], frozenset())
    assert got2[0].source == "open"


def test_gate_owners_names_the_leg_that_declares_each_gate() -> None:
    from types import SimpleNamespace

    from updown.orchestration.live_review.health import gate_of, gate_owners

    books = [
        SimpleNamespace(playbook_id="crash", short_label="급락 되돌림", entry_ref_vol_pct=object()),
        SimpleNamespace(
            playbook_id="tri",
            short_label="삼각 숏",
            entry_ref_return_band=object(),
            entry_ref_surge_cap=object(),
        ),
        SimpleNamespace(
            playbook_id="old", short_label="옛", listed=False, entry_ref_vol_pct=object()
        ),
    ]
    got = gate_owners(books)
    assert got["ref_volpct"] == ["급락 되돌림"]
    assert got["ref_band"] == ["삼각 숏"] and got["ref_surge"] == ["삼각 숏"]
    assert got["ref_sma"] == []
    assert gate_of("session_entry_ref_volpct_held") == "ref_volpct"
    assert gate_of("session_entry_gate_fit") == "session_entry_gate_fit"
