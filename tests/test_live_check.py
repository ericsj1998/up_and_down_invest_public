"""라이브 점검(live-check) — 판단 재료 계산 시험.

## 무엇을 막으려는 시험인가

1. 서버 상태 한 장(`health.txt`)의 모르는 줄 · 빈 칸이 점검을 깨뜨리는 것.
2. 거래소 포지션에 손절이 없거나 원장과 어긋나는데 ✅ 로 나오는 것 — 진짜 돈의 위험이다.
3. API 재기동 전 자가 점검 기록이 "안 풀린 문제" 로 영영 남아 보이는 것.
4. 리더가 없는데(판이 조용히 멈춤) ⛔ 가 안 나오는 것.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from updown.orchestration.live_review.check import (
    Bar1,
    Health,
    PositionCheck,
    active_audits,
    macd_hist,
    now_rows,
    parse_health,
    position_checks,
    story_of,
)
from updown.orchestration.live_review.snapshot import Run, Snapshot, Trade

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


def _trade(**kw: Any) -> Trade:
    base: dict[str, Any] = {
        "run_id": "r1",
        "symbol": "NEAR_USDT",
        "run_playbook_id": "pb",
        "trade_id": "abcd1234ffff",
        "playbook": "private_strategy@0.1.0",
        "direction": "롱",
        "outcome": "보유중",
        "actor": "시스템",
        "placed_at": NOW - timedelta(hours=20),
        "opened_at": NOW - timedelta(hours=20),
        "closed_at": None,
        "entry": Decimal("5.3"),
        "exit_price": None,
        "planned_stop": Decimal("4.78"),
        "planned_target": Decimal("7.1"),
        "leverage": Decimal("4"),
        "filled_leverage": Decimal("4"),
        "margin_used": Decimal("20"),
        "contracts": 15,
        "funding_paid": None,
        "fee_actual": None,
        "realized_adjust": None,
        "half_price": None,
        "half_at": None,
    }
    base.update(kw)
    return Trade(**base)


def _snap(
    trades: list[Trade], exchange: dict[str, Any], events: list[dict[str, Any]] | None = None
) -> Snapshot:
    run = Run(
        "r1",
        "live1",
        "NEAR_USDT",
        "pb",
        "pb",
        Decimal("4"),
        Decimal("15"),
        NOW - timedelta(days=9),
        None,
        "",
    )
    fund = {
        "legs": [{"attribution": "private_strategy@0.1.0", "symbols": ["NEAR_USDT"]}],
        "twr": {"equity": "100", "twr": "0.9", "twr_peak": "1.0"},
        "core_twr": {"twr": "1", "twr_peak": "1"},
        "drawdown_brake": {"at": "0.05"},
    }
    return Snapshot(Path(), NOW, [run], trades, [], fund, events or [], exchange)


def _pos(size: int = 15) -> dict[str, Any]:
    return {
        "contract": "NEAR_USDT",
        "size": size,
        "entry_price": "5.3",
        "mark_price": "5.25",
        "margin": "20",
    }


def _stop(price: str = "4.7866", size: int = -15) -> dict[str, Any]:
    return {"initial": {"contract": "NEAR_USDT", "size": size}, "trigger": {"price": price}}


def test_parse_health_reads_known_lines_and_skips_unknown() -> None:
    h = parse_health(
        "at|2026-10-11T09:00:00Z\napi|updown_live-api-1\n"
        "container|updown_live-api-1|Up 2 days (healthy)|img:v1.40.0\n"
        "env|LIVE_ORDERS|1\nleader_pttl|25000\nleader_health|True\n"
        "halt|0|2026-10-02T07:05:00Z\nmem|911|700|200\nswap|2047|600\ncpu|4|91|0\ndisk|69%\n"
        "모르는줄|x\n빈줄\n"
    )
    assert h.api == "updown_live-api-1"
    assert h.env["LIVE_ORDERS"] == "1"
    assert h.leader_health is True and h.leader_pttl == 25000
    assert h.mem == (911, 700, 200) and h.swap == (2047, 600) and h.disk_pct == 69


def test_position_with_matching_stop_has_no_problem() -> None:
    checks, ghosts = position_checks(
        _snap([_trade()], {"positions": [_pos()], "price_orders": [_stop()]})
    )
    assert len(checks) == 1 and checks[0].problems == () and ghosts == []


def test_missing_stop_and_orphan_are_flagged() -> None:
    checks, _ = position_checks(_snap([], {"positions": [_pos()], "price_orders": []}))
    probs = " ".join(checks[0].problems)
    assert "원장에 열린 매매가 없다" in probs and "손절이 없다" in probs


def test_stop_far_from_plan_and_partial_size_are_flagged() -> None:
    exchange = {"positions": [_pos()], "price_orders": [_stop(price="4.5", size=-5)]}
    probs = " ".join(position_checks(_snap([_trade()], exchange))[0][0].problems)
    assert "다 못 닫는다" in probs and "계획 손절" in probs


def test_ledger_open_without_position_is_a_ghost() -> None:
    _, ghosts = position_checks(_snap([_trade()], {"positions": [_pos(0)], "price_orders": []}))
    assert ghosts and "포지션이 없다" in ghosts[0]


def test_audits_before_restart_are_dropped() -> None:
    ev = [
        {
            "event_type": "live_audit_found",
            "ts": "2026-10-08T00:00:00Z",
            "payload": {"run": "a", "code": "wallet_drift"},
        },
        {
            "event_type": "live_audit_found",
            "ts": "2026-10-10T00:00:00Z",
            "payload": {"run": "b", "code": "book_dry"},
        },
        {
            "event_type": "live_audit_cleared",
            "ts": "2026-10-10T01:00:00Z",
            "payload": {"run": "b", "code": "book_dry"},
        },
    ]
    assert len(active_audits(ev)) == 1
    assert active_audits(ev, datetime(2026, 10, 9, tzinfo=UTC)) == {}


def test_no_leader_is_bad() -> None:
    snap = _snap([_trade()], {"positions": [_pos()], "price_orders": [_stop()]})
    checks, ghosts = position_checks(snap)
    rows = now_rows(snap, Health(leader_health=False, leader_pttl=-2), checks, ghosts, NOW)
    leader = next(r for r in rows if r.item == "거래 리더")
    assert leader.status == "bad"


def test_story_r_and_btc_relative() -> None:
    t = _trade()
    check = PositionCheck(
        "NEAR_USDT",
        15,
        Decimal("5.3"),
        Decimal("5.247"),
        Decimal("-0.8"),
        Decimal("20"),
        "4",
        None,
        t,
        Decimal("4.78"),
        -15,
        (),
    )
    bars = [Bar1(NOW - timedelta(hours=2), Decimal("5.42"), Decimal("5.15"), Decimal("5.25"))]
    btc = [
        Bar1(NOW - timedelta(hours=2), Decimal("1"), Decimal("1"), Decimal("100")),
        Bar1(NOW - timedelta(hours=1), Decimal("1"), Decimal("1"), Decimal("101")),
    ]
    st = story_of(check, bars, btc, {}, NOW)
    assert st.r_now is not None and Decimal("-0.11") < st.r_now < Decimal("-0.09")
    assert st.btc_pct == Decimal(1)
    assert st.mae_pct is not None and st.mae_pct < 0 < (st.mfe_pct or 0)


def test_macd_hist_needs_enough_bars() -> None:
    assert macd_hist([Decimal(1)] * 10) is None
    got = macd_hist([Decimal(100 + i) for i in range(60)])
    assert got is not None and got[0] >= 0
