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


def test_trend_supply_counts_doublers_and_needs_history() -> None:
    from updown.orchestration.live_review.check import trend_supply

    up = [Decimal(1)] * 91 + [Decimal("2.5")]  # 90일 전 1 → 지금 2.5 = +150%
    flat = [Decimal(1)] * 92
    young = [Decimal(1)] * 30  # 상장 30일 — 못 잰다
    closes = {"A": up, "B": flat, "C": flat, "D": flat, "E": flat, "F": young}
    share, n = trend_supply(closes, 90, Decimal("1.0"), 0, 5)
    assert n == 5 and share == 0.2
    # 하루 전으로 재면 A 도 아직 안 올랐다
    assert trend_supply(closes, 90, Decimal("1.0"), 1, 5)[0] == 0.0
    # 잴 종목이 모자라면 None
    assert trend_supply({"A": up}, 90, Decimal("1.0"), 0, 5)[0] is None


def test_macd_hist_needs_enough_bars() -> None:
    assert macd_hist([Decimal(1)] * 10) is None
    got = macd_hist([Decimal(100 + i) for i in range(60)])
    assert got is not None and got[0] >= 0


def _run(symbol: str, key: str, *, closed: bool = False) -> Run:
    return Run(
        key,
        key,
        symbol,
        "pb",
        "pb",
        None,
        Decimal("2.14"),
        NOW - timedelta(days=11),
        NOW if closed else None,
        "지움" if closed else "",
        {},
    )


def test_fund_bound_to_a_dead_run_is_a_warning() -> None:
    """T605 — 펀드 `runs` 가 가리키는 판 키가 열린 판이 아니면(죽은 판을 묶음) ⚠️.

    10-16 전환이 반쪽으로 끝나 핵심 8 종의 펀드 묶음은 옛 키를 가리킨 채 새 판이 떴고, 그 판은
    모든 봉에서 `awaiting_fund` 로 막혔는데 "막힌 후보 0건 ✅" 로 가렸다. 깔때기 수는 재기동을
    넘어 쌓여 시각 판정에 못 쓴다 —
    펀드 묶음 ↔ 열린 판 키 대조가 사실이다.
    """
    from updown.orchestration.live_review.check import blocked_row, unbound_members

    runs = [
        _run("BTC_USDT", "livefed01907"),
        _run("ALGO_USDT", "live7fe0fa61"),
        _run("BTC_USDT", "livefe5d17f4", closed=True),
    ]
    fund = {
        "runs": {
            "BTC_USDT": "livefe5d17f4",
            "ALGO_USDT": "live7fe0fa61",
            "LINK_USDT": "live22862d93",
        }
    }
    snap = Snapshot(Path(), NOW, runs, [], [], fund, [], {}, blocked=[])
    # LINK 는 열린 판이 없다 — "열린 판 · 펀드 선언" 줄의 몫이라 여기선 안 센다.
    assert unbound_members(snap) == ["BTC_USDT(e5d17f4→fed01907)"[:0] + "BTC_USDT(5d17f4→d01907)"]
    row = blocked_row(snap, Health(), NOW, 7)
    assert row.status == "warn"
    assert "BTC_USDT(5d17f4→d01907)" in row.value
    # 막힘 기록이 없는 옛 덤프라도 묶음 어긋남은 ⚠️ 로 올린다.
    old = Snapshot(Path(), NOW, runs, [], [], fund, [], {}, blocked=None)
    assert blocked_row(old, Health(), NOW, 7).status == "warn"


def test_fund_bound_to_the_open_runs_stays_ok() -> None:
    from updown.orchestration.live_review.check import blocked_row, unbound_members

    runs = [_run("NEAR_USDT", "live1")]
    snap = Snapshot(Path(), NOW, runs, [], [], {"runs": {"NEAR_USDT": "live1"}}, [], {}, blocked=[])
    assert unbound_members(snap) == []
    assert blocked_row(snap, Health(), NOW, 7).status == "ok"
    # 펀드 파일에 runs 가 없으면(옛 형식) 조용히 지나간다.
    assert unbound_members(Snapshot(Path(), NOW, runs, [], [], {}, [], {}, blocked=[])) == []


def test_snapshot_reads_old_rows_without_the_contract_adjust_column(tmp_path: Path) -> None:
    """T606 - `contract_adjust` 는 맨 끝 칸이라 옛 스냅샷(칸 하나 적음)도 그대로 읽힌다."""
    from updown.orchestration.live_review.snapshot import TRADE_COLUMNS, load_snapshot

    assert TRADE_COLUMNS[-1] == "contract_adjust" and TRADE_COLUMNS[-2] == "evidence_json"
    old = [""] * (len(TRADE_COLUMNS) - 1)
    old[TRADE_COLUMNS.index("run_id")] = "r1"
    old[TRADE_COLUMNS.index("symbol")] = "NEAR_USDT"
    old[TRADE_COLUMNS.index("trade_id")] = "abcd1234ffff"
    old[TRADE_COLUMNS.index("entry")] = "5.3"
    old[TRADE_COLUMNS.index("evidence_json")] = '{"a": 1}'
    new = [*old, "-0.97"]
    new[TRADE_COLUMNS.index("trade_id")] = "ffff4321dcba"  # 같은 id 는 한 줄로 합쳐진다
    tab, nl = chr(9), chr(10)
    (tmp_path / "trades.tsv").write_text(tab.join(old) + nl + tab.join(new) + nl, encoding="utf-8")
    snap = load_snapshot(tmp_path)
    assert [t.contract_adjust for t in snap.trades] == [None, Decimal("-0.97")]
    assert all(t.evidence == {"a": 1} and t.entry == Decimal("5.3") for t in snap.trades)
