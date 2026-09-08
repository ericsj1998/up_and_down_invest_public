"""한 종목의 주문·매매 원장 행을 시간순으로 — api 컨테이너 안에서 (`remote.sh` 가 .py 를 거기서 돌린다).

    SYMBOL=NEAR_USDT HOURS=14 bash scripts/ops/remote.sh scripts/ops/trace_orders.py

출력은 시각 · 역할 · 상태 · 거래소 주문 id 끝 4자리 · 계약 수 · 가격 · 거래소 원문의 생성/완료 시각뿐이다.
키·잔고는 찍지 않는다. 왜: 체결 시각(거래소) 과 원장 기록 시각(DB) 의 텀을 재려고 (2026-09-06 고아 경보).
"""

# ruff: noqa: E501 — 운영 출력 한 줄이 길다 (사람이 읽는 표)
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import psycopg

SYMBOL = os.environ.get("SYMBOL", "NEAR_USDT")
HOURS = int(os.environ.get("HOURS", "14"))
DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
since = datetime.now(UTC) - timedelta(hours=HOURS)


def ts(v: object) -> str:
    """거래소 epoch(초 · 소수) 또는 datetime → HH:MM:SS (UTC)."""
    if v in (None, "", 0):
        return "—"
    try:
        if isinstance(v, datetime):
            return v.astimezone(UTC).strftime("%H:%M:%S")
        return datetime.fromtimestamp(float(str(v)), tz=UTC).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return str(v)[:8]


with psycopg.connect(DSN) as conn:
    runs = conn.execute(
        "select id, opened_at, closed_at from wf_runs where symbol = %s order by opened_at desc limit 3",
        (SYMBOL,),
    ).fetchall()
    print(f"== wf_runs {SYMBOL}: {[(str(r[0])[:8], ts(r[1]), ts(r[2])) for r in runs]}")
    rows = conn.execute(
        """
        select o.created_at, o.updated_at, o.trade_id, o.role, o.status, o.exchange_order_id,
               o.contracts, o.price, o.raw_json
        from wf_orders o join wf_runs r on r.id = o.run_id
        where r.symbol = %s and o.updated_at >= %s
        order by o.created_at
        """,
        (SYMBOL, since),
    ).fetchall()
    print(f"== wf_orders ({len(rows)} rows · since {HOURS}h · UTC)")
    print(
        "created  updated  trade_id role      status    oid   contracts price     ex.create ex.finish finish_as"
    )
    for created, updated, trade_id, role, status, oid, contracts, price, raw in rows:
        raw = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        print(
            f"{ts(created)} {ts(updated)} {str(trade_id)[:8]:8s} {str(role)[:9]:9s} {str(status)[:9]:9s} "
            f"{str(oid)[-4:]:>5s} {str(contracts)[:9]:>9s} {str(price)[:9]:>9s} "
            f"{ts(raw.get('create_time')):>9s} {ts(raw.get('finish_time')):>9s} {str(raw.get('finish_as', ''))[:10]}"
        )
    trades = conn.execute(
        """
        select t.trade_id, t.outcome, t.opened_at, t.closed_at, t.entry
        from wf_trades t join wf_runs r on r.id = t.run_id
        where r.symbol = %s and coalesce(t.closed_at, t.opened_at) >= %s
        order by t.opened_at
        """,
        (SYMBOL, since),
    ).fetchall()
    print(f"== wf_trades ({len(trades)} rows)")
    for trade_id, status, opened, closed, entry in trades:
        print(
            f"  {str(trade_id)[:8]} {status!s:10s} opened {ts(opened)} closed {ts(closed)} entry {str(entry)[:9]}"
        )
    cols = conn.execute(
        "select column_name from information_schema.columns where table_name='wf_trades' order by ordinal_position"
    ).fetchall()
    print("== wf_trades columns:", [c[0] for c in cols])
    meta = conn.execute(
        "select meta_json->'pending_entry' from wf_runs where symbol = %s and closed_at is null",
        (SYMBOL,),
    ).fetchall()
    for (pending,) in meta:
        if not pending:
            print("== pending_entry: (없음)")
            continue
        rec = pending.get("record", {}) if isinstance(pending, dict) else {}
        print(
            "== pending_entry: tickets=",
            pending.get("tickets") if isinstance(pending, dict) else "?",
            "placed_at=",
            rec.get("placed_at"),
            "planned_stop=",
            rec.get("planned_stop"),
            "waiting_until=",
            pending.get("waiting_until") if isinstance(pending, dict) else "?",
        )
