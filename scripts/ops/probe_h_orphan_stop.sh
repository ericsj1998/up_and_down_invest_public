#!/usr/bin/env bash
# H_USDT 고아 손절(포지션 없음 · 크기 0) — 어느 판 · 어느 매매가 걸었고 어떻게 닫혔나(읽기만 · 키 · 주문 id 안 찍음)
#   bash scripts/ops/remote.sh scripts/ops/probe_h_orphan_stop.sh
set -u
cd ~/updown || exit 1
A=$(docker ps --filter name=updown_live-api --filter status=running --format '{{.Names}}' | head -1)
PG=updown_live-postgres-1
SYM=${SYM:-H_USDT}
echo "=== 거래소 조건부($SYM)"
docker exec -i -e SYM="$SYM" "$A" python - <<'PY'
import asyncio, datetime as dt, os
from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient
async def main():
    c = GateTradeClient(os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"], base_url=LIVE_BASE_URL)
    sym = os.environ["SYM"]
    for row in await c.list_stops(sym):
        ini = row.get("initial", {}) or {}; trg = row.get("trigger", {}) or {}
        made = row.get("create_time")
        when = dt.datetime.fromtimestamp(float(made), dt.UTC).strftime("%Y-%m-%d %H:%M:%S") if made else "?"
        print("  size=", ini.get("size"), "trigger=", trg.get("price"), "rule=", trg.get("rule"),
              "reduce_only=", ini.get("reduce_only"), "close=", ini.get("close"),
              "auto_size=", ini.get("auto_size"), "text=", ini.get("text") or row.get("text"),
              "made=", when, "id_tail=", str(row.get("id", ""))[-4:], "status=", row.get("status"))
    try:
        rows = await c._request("GET", "/futures/usdt/position_close", params={"contract": sym, "limit": "10"})  # noqa: SLF001
        print("=== 거래소 포지션 닫힘 기록(최근 10)")
        for r in rows or []:
            t = r.get("time")
            print("  ", dt.datetime.fromtimestamp(float(t), dt.UTC).strftime("%Y-%m-%d %H:%M:%S") if t else "?",
                  "side=", r.get("side"), "pnl=", r.get("pnl"), "text=", r.get("text"))
    except Exception as exc:  # 이 메서드 이름이 다르면 건너뛴다
        print("position_close 못 읽음:", type(exc).__name__, str(exc)[:120])
    await c.aclose()
asyncio.run(main())
PY
echo "=== 판(wf_runs · $SYM)"
docker exec -i "$PG" psql -U updown -d updown -tA -v sym="$SYM" <<'SQL'
select left(key, 14) || ' | ' || kind || ' | live ' || live || ' | ' || playbook || ' | opened ' || to_char(opened_at,'MM-DD HH24:MI') || ' | closed ' || coalesce(to_char(closed_at,'MM-DD HH24:MI'),'-') || ' | ' || coalesce(closed_reason,'-')
  from wf_runs where symbol = :'sym' order by opened_at desc limit 8;
select '=== 매매(wf_trades · 최근 8)';
select t.trade_id || ' | ' || t.direction || ' | ' || t.outcome || ' | ' || t.actor || ' | opened ' || coalesce(to_char(t.opened_at,'MM-DD HH24:MI'),'-') || ' | closed ' || coalesce(to_char(t.closed_at,'MM-DD HH24:MI'),'-') || ' | entry ' || t.entry || ' | stop ' || t.planned_stop || ' | exit ' || coalesce(t.exit_price::text,'-') || ' | ' || left(t.note, 80)
  from wf_trades t join wf_runs r on r.id = t.run_id where r.symbol = :'sym' order by t.placed_at desc limit 8;
select '=== 주문(wf_orders · 손절 · 청산 역할 · 최근 12)';
select to_char(o.created_at,'MM-DD HH24:MI:SS') || ' | ' || o.role || ' | ' || o.status || ' | ' || coalesce(o.price::text,'-') || ' | ' || o.contracts || ' | ' || left(o.error, 80)
  from wf_orders o join wf_runs r on r.id = o.run_id where r.symbol = :'sym' order by o.created_at desc limit 12;
SQL
echo "=== event_logs($SYM · 손절 · 청산 · 잔재 · 최근 25)"
docker exec -i "$PG" psql -U updown -d updown -tA -v sym="$SYM" <<'SQL'
select to_char(ts,'MM-DD HH24:MI:SS') || ' | ' || event_type || ' | ' || left(payload_json::text, 200)
  from event_logs
 where payload_json::text like '%' || :'sym' || '%'
   and (event_type ilike '%stop%' or event_type ilike '%close%' or event_type ilike '%leftover%' or event_type ilike '%exit%' or event_type ilike '%flat%' or event_type ilike '%orphan%')
 order by ts desc limit 25;
SQL
