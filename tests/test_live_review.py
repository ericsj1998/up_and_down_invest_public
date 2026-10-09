"""라이브 매매 분석기(T444) — 순수 계산 시험.

## 무엇을 막으려는 시험인가

1. 거래소 닫힌 포지션 기록을 **엉뚱한 매매에** 붙이는 것 — 매매 id 앞자리 + 종목 + 시각 창 셋 다
   맞아야 한다.
2. 같은 봉에 익절 · 손절이 둘 다 닿았을 때 익절로 세는 것(낙관) — 손절 먼저.
3. 숏 매매의 R 부호가 뒤집히는 것.
4. 손실 몫의 합이 100% 가 안 되는 것.
5. (T451 G) 문 표가 세션과 다른 판정을 내는 것 · 문턱까지 거리 부호가 뒤집히는 것 ·
   근접 표가 탐지기와 다른 답을 내는 것 · 기대값 칸의 잘못된 값이 조용히 "—" 가 되는 것.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.analysis.detectors.base import ParamValue, RuleParams
from updown.analysis.indicators.bands import bollinger
from updown.analysis.indicators.reference import RefRegime
from updown.analysis.playbook.types import (
    Family,
    Playbook,
    RefReturnBand,
    RefSurgeCap,
    RefVolPct,
    SmaTilt,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.orchestration.live_review.attribution import (
    leg_table,
    money_of,
    outside_money,
    trade_prefix_of_text,
)
from updown.orchestration.live_review.conditions import (
    BreakoutRule,
    ConditionView,
    RefExtras,
    gate_rows,
    gate_series,
    gate_spans,
    leg_table_lines,
    need_close,
    next_bar_need,
    probe_bar,
    read_expectations,
    replay_gap,
    session_hold,
    spans_by_gate,
    tilt_rows,
)
from updown.orchestration.live_review.excursion import Bar, excursion, grid_outcome
from updown.orchestration.live_review.health import funnel_totals, playbook_spans, summarize_events
from updown.orchestration.live_review.report import render
from updown.orchestration.live_review.snapshot import Run, Snapshot, Trade

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
            playbook_id="old", short_label="급락 되돌림", listed=False, entry_ref_vol_pct=object()
        ),
    ]
    got = gate_owners(books)
    assert got["ref_volpct"] == ["급락 되돌림"]
    assert got["ref_band"] == ["삼각 숏"] and got["ref_surge"] == ["삼각 숏"]
    assert got["ref_sma"] == []
    assert gate_of("session_entry_ref_volpct_held") == "ref_volpct"
    assert gate_of("session_entry_gate_fit") == "session_entry_gate_fit"


def test_fill_basis_uses_the_entry_fill_of_this_trade_only() -> None:
    from updown.orchestration.live_review.attribution import fill_basis

    t = _trade(trade_id="abcdef12-rest", symbol="SOL_USDT")
    orders = [
        {
            "contract": "SOL_USDT",
            "text": "t-13dcac-abcdef12-en-0",
            "fill_price": "99.7",
            "create_time": str((T0 + timedelta(minutes=10)).timestamp()),
            "finish_as": "filled",
        },
        {
            "contract": "SOL_USDT",
            "text": "t-13dcac-abcdef12-cl-0",
            "fill_price": "103",
            "create_time": str((T0 + timedelta(hours=3)).timestamp()),
            "finish_as": "filled",
        },
        {
            "contract": "BTC_USDT",
            "text": "t-13dcac-abcdef12-en-0",
            "fill_price": "1",
            "create_time": str(T0.timestamp()),
            "finish_as": "filled",
        },
        {
            "contract": "SOL_USDT",
            "text": "web_p_1",
            "fill_price": "50",
            "create_time": str(T0.timestamp()),
            "finish_as": "filled",
        },
    ]
    got = fill_basis(t, orders)
    assert got is not None
    assert got[0] == Decimal("99.7") and got[1] == T0 + timedelta(minutes=10)
    assert fill_basis(_trade(trade_id="zzzz0000"), orders) is None


# ------------------------------------------- T451 G (T445 5단계) — 조건 · 근접 · 재현 · 기대값


def _book(
    pid: str,
    label: str,
    *,
    band: bool = False,
    surge: bool = False,
    vpct: bool = False,
) -> Playbook:
    return Playbook(
        playbook_id=pid,
        version="0.1.0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H4,
        regimes=(),
        primary_family=Family.TREND,
        short_label=label,
        entry_ref_return_band=(
            RefReturnBand(bars=3, low=Decimal("-0.15"), high=Decimal("0.15")) if band else None
        ),
        entry_ref_surge_cap=(
            RefSurgeCap(days=7, high=Decimal("0.08204173132170967")) if surge else None
        ),
        entry_ref_vol_pct=(RefVolPct(bars=120, rank=500, low=Decimal("0.816")) if vpct else None),
    )


def _legs3() -> list[Playbook]:
    return [
        _book("tri", "삼각 숏", band=True, surge=True),
        _book("crash", "급락 되돌림", vpct=True),
        _book("brk", "돌파 롱"),
    ]


class TestGateTable:
    def test_value_threshold_pass_and_distance(self) -> None:
        regime = RefRegime(
            above=None,
            ret=Decimal("0.2684"),
            surge=Decimal("0.03"),
            sma_down=None,
            vol=(),
            vol_pct=Decimal("0.416"),
        )
        rows = gate_rows(_legs3(), regime, RefExtras())
        by = {(r.leg_id, r.gate): r for r in rows}
        band = by[("tri", "ref_band")]
        assert band.passed is False and band.margin == Decimal("0.15") - Decimal("0.2684")
        surge = by[("tri", "ref_surge")]
        assert surge.passed is True
        assert surge.margin == Decimal("0.08204173132170967") - Decimal("0.03")
        vol = by[("crash", "ref_volpct")]
        assert vol.passed is False and vol.margin == Decimal("0.416") - Decimal("0.816")
        assert vol.percent is False
        assert by[("brk", "")].passed is True  # 기준 문 없는 다리도 한 줄
        # 세션 `entry_hold` 그대로 — 띠가 먼저 · 문 없는 다리는 None
        legs = _legs3()
        assert session_hold(legs[0], regime) == "ref_band"
        assert session_hold(legs[1], regime) == "ref_volpct"
        assert session_hold(legs[2], regime) is None

    def test_unknown_regime_holds_like_the_runner_fallback(self) -> None:
        rows = gate_rows(_legs3(), None, RefExtras())
        assert [r.passed for r in rows if r.gate] == [False, False, False]
        assert all(r.margin is None for r in rows if r.gate)
        assert session_hold(_legs3()[0], None) == "ref_band"

    def test_spans_group_runs_of_the_same_state(self) -> None:
        states: list[bool | None] = [True, True, False, None, None]
        pts = [(T0 + timedelta(hours=4 * k), s) for k, s in enumerate(states)]
        got = gate_spans(pts)
        assert [(s.state, s.bars) for s in got] == [(True, 2), (False, 1), (None, 2)]
        assert got[0].first_end == T0 and got[0].last_end == T0 + timedelta(hours=4)

    def test_series_uses_reference_regime_bar_by_bar(self) -> None:
        inst = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
        closes = [Decimal(100)] * 40 + [Decimal(130)] * 2  # 마지막 둘: 3봉 수익률 +30% → 띠 밖
        start = datetime(2026, 9, 1, tzinfo=UTC)
        bars = [
            Candle(
                instrument=inst,
                timeframe=Timeframe.H4,
                ts=start + timedelta(hours=4 * k),
                open=c,
                high=c,
                low=c,
                close=c,
                volume=Decimal(1),
            )
            for k, c in enumerate(closes)
        ]
        legs = [_book("tri", "삼각 숏", band=True)]
        since = bars[-4].ts + timedelta(hours=4)
        series = gate_series(bars, legs, since)
        assert [m.end for m in series] == [b.ts + timedelta(hours=4) for b in bars[-4:]]
        assert [m.rows[0].passed for m in series] == [True, True, False, False]
        assert series[-1].regime is not None and series[-1].regime.ret == Decimal("0.3")
        spans = spans_by_gate(series)[("tri", "ref_band")]
        assert [(s.state, s.bars) for s in spans] == [(True, 2), (False, 2)]


RULE_VALUES: dict[str, ParamValue] = {
    "bb_period": 20,
    "bb_k": Decimal("2"),
    "vol_period": 20,
    "vol_multiple": Decimal("2.0"),
    "sl_atr": Decimal("0"),
    "floor_sl_atr": Decimal("0.2"),
    "dir_period": 20,
    "dir_bars": 5,
    "entry_stop_floor_pct": Decimal("1.4"),
    "pen_min_atr": Decimal("0.75"),
}
"""`config/rules/private_strategy.yml` 0.5 의 진입 조건 값 — 시험은 설정 파일 대신 고정 입력."""


def _params() -> RuleParams:
    return RuleParams(rule_id="private_strategy", version="0.5", values=RULE_VALUES)


def _hours(last_close: str | None, last_volume: str = "50", n: int = 80) -> list[Candle]:
    """평평한 1H 봉(종가 100 · 고저 ±0.5 · 거래량 10) — 마지막 봉만 바꿔 돌파를 만든다."""
    inst = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    out = [
        Candle(
            instrument=inst,
            timeframe=Timeframe.H1,
            ts=start + timedelta(hours=k),
            open=Decimal(100),
            high=Decimal("100.5"),
            low=Decimal("99.5"),
            close=Decimal(100),
            volume=Decimal(10),
        )
        for k in range(n)
    ]
    if last_close is not None:
        out.append(
            Candle(
                instrument=inst,
                timeframe=Timeframe.H1,
                ts=start + timedelta(hours=n),
                open=Decimal(100),
                high=Decimal(last_close) + Decimal("0.5"),
                low=Decimal(101),
                close=Decimal(last_close),
                volume=Decimal(last_volume),
            )
        )
    return out


class TestBreakoutProximity:
    def test_need_close_solves_band_plus_penetration(self) -> None:
        rule = BreakoutRule.of(_params())
        prev = [Decimal(100)] * 19
        got = need_close(prev, Decimal(2), rule)
        assert got is not None
        # 앞 19봉이 100 이면 상단 = 100 + (c - 100) x (1 + 2 sqrt 19) / 20
        # → c - 상단 = 1.5 에서 c ≈ 102.9177
        assert abs(float(got) - 102.9177) < 1e-3
        upper = bollinger([*prev, got], period=20, multiple=Decimal(2)).upper[-1]
        assert upper is not None and got - upper >= Decimal("1.5") - Decimal("1e-6")
        below = got - Decimal("0.01")
        upper2 = bollinger([*prev, below], period=20, multiple=Decimal(2)).upper[-1]
        assert upper2 is not None and below - upper2 < Decimal("1.5")

    def test_probe_matches_the_detector(self) -> None:
        hit = probe_bar(_hours("105"), _params())
        assert hit is not None and hit.fired and hit.missing == ()
        assert hit.vol_ratio == Decimal(5) and hit.direction == 0
        assert hit.need is not None and hit.need_pct is not None and hit.need_pct < 0
        thin = probe_bar(_hours("105", last_volume="15"), _params())
        assert thin is not None and not thin.fired and thin.missing == ("거래량",)
        # 종가 101.6 은 가격(≥ 101.4588)은 넘지만 저가 101 이라 손절폭 0.79% < 1.4%
        narrow = probe_bar(_hours("101.6"), _params())
        assert narrow is not None and not narrow.fired and narrow.missing == ("손절폭",)
        low = probe_bar(_hours("101.2"), _params())
        assert low is not None and not low.fired and low.missing == ("가격", "손절폭")
        assert low.need_pct is not None and low.need_pct > 0

    def test_next_bar_thresholds(self) -> None:
        got = next_bar_need(_hours(None), _params())
        assert got is not None
        # ATR = 1(고저 1 · 평평) → 상단 + 0.75 = c 에서 c ≈ 100 + 0.75 / 0.51411
        assert got.need is not None and abs(float(got.need) - 101.4588) < 1e-3
        assert got.need_volume is not None and abs(float(got.need_volume) - 20) < 1e-9
        assert got.low_drop_pct is not None
        assert abs(float(got.low_drop_pct) - (1.4 - 0.2 / float(got.need) * 100)) < 1e-6
        assert got.bar_ts == datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=80)

    def test_tilt_rows_use_the_declaration(self) -> None:
        tilt = SmaTilt(
            timeframe=Timeframe.D1,
            period=2,
            mult=Decimal(0),
            back=1,
            low=Decimal("-2"),
            high=Decimal("-0.5"),
        )
        book = replace(_book("brk", "돌파 롱"), sma_tilts=(tilt,))
        # SMA2 100 → 99.5 = -0.5% → 띠 밖(위 끝 제외)
        got = tilt_rows(book, {Timeframe.D1: [Decimal(100), Decimal(100), Decimal(99)]})
        assert got[0].value == Decimal("-0.5") and got[0].inside is False
        got2 = tilt_rows(book, {Timeframe.D1: [Decimal(100), Decimal(100), Decimal(98)]})
        assert got2[0].inside is True and got2[0].mult == 0
        assert tilt_rows(book, {})[0].value is None


GAP_MD = """<!-- 재현 JSON 40 / 40 -->

## 다리별 합계 (짝 · 명목 대비 %)

| 다리 | 짝 n | 차이 % 합 |
|---|---|---|
| 돌파 롱 | 8 | -0.60 |

## 원인 묶음별 합계 (짝)

| 묶음 | 짝 n |
|---|---|
"""


class TestReplayAndExpectations:
    def test_replay_gap_summary(self) -> None:
        summary: dict[str, object] = {
            "pairs": 19,
            "pairs_strict": 15,
            "rep_only": 15,
            "live_only": 3,
            "live_system": 22,
            "rep_trades": 34,
            "gap_sum": "10.05",
            "parts": {"entry_part": "-5.54", "exit_part": "14.26"},
            "live_usdt": "-104.05",
            "orphan_funding": 23,
        }
        got = replay_gap(summary, GAP_MD, T0)
        assert got.pairs == 19 and got.pairs_strict == 15 and got.gap_sum == Decimal("10.05")
        assert got.parts["exit_part"] == Decimal("14.26")
        assert got.leg_table == (
            "| 다리 | 짝 n | 차이 % 합 |",
            "|---|---|---|",
            "| 돌파 롱 | 8 | -0.60 |",
        )
        assert leg_table_lines("표 없음") == ()

    def test_read_expectations(self) -> None:
        text = (
            'source: "판 997"\n'
            "legs:\n"
            "  a:\n"
            "    win_rate_pct: 41.3\n"
            "    mean_r: 0.34\n"
            "    loss_share_pct: null\n"
            '    note: "돌파 롱"\n'
            "  b: {}\n"
        )
        legs, source = read_expectations(text)
        assert source == "판 997"
        assert legs["a"] == {
            "note": "돌파 롱",
            "win_rate_pct": 41.3,
            "mean_r": 0.34,
            "loss_share_pct": None,
        }
        assert legs["b"]["win_rate_pct"] is None
        with pytest.raises(ValueError, match="숫자 또는 null"):
            read_expectations("legs:\n  a:\n    mean_r: 높음\n")

    def test_the_shipped_expectations_file_parses(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "config" / "live_review_expectations.yml"
        legs, source = read_expectations(path.read_text(encoding="utf-8"))
        assert source
        assert set(legs) == {
            "private_strategy",
            "private_strategy",
            "private_strategy",
            "private_strategy",
            "private_strategy",
            "private_strategy",
        }

    def test_render_has_the_new_sections(self) -> None:
        from pathlib import Path

        snap = Snapshot(
            path=Path("snap"),
            taken_at=T0,
            runs=[],
            trades=[],
            orders=[],
            fund={},
            events=[],
            exchange={},
        )
        events = summarize_events([])

        def page(**kw: Any) -> str:
            return render(snap, [], [], {}, [], {}, events, {}, {}, {}, {}, None, 30, [], **kw)

        skipped = page(
            conditions=ConditionView(fund_id="f", skipped="봉 없음"), replay_command="명령"
        )
        assert "## 1-2. 지금 문 상태" in skipped and "생략: 봉 없음" in skipped
        assert "## 1-3. 돌파 롱 근접" in skipped
        assert "## 2-1. 라이브 대 재현" in skipped and "재현 없음 — `명령`" in skipped
        gap = replay_gap({"pairs": 2, "pairs_strict": 1, "gap_sum": "1.5"}, GAP_MD, T0)
        full = page(replay=gap, replay_command="명령", expect_source="판 997")
        assert "**+1.50**" in full and "| 돌파 롱 | 8 | -0.60 |" in full
        assert "기대값 출처: 판 997" in full
