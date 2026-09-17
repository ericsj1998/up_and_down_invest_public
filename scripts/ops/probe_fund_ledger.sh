#!/usr/bin/env bash
# 라이브 펀드 · 거래 원장 읽기 전용 프로브 — 값은 거래·잔고 숫자만 (키·토큰 없음)
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --format "{{.Names}}" | grep -E "updown_live-api" | head -1)
PG=updown_live-postgres-1
echo "=== fund json (keys · budget · events)"
docker exec "$API" sh -c 'for f in /app/logs/funds/*.json; do [ -f "$f" ] && python -c "import json,sys; d=json.load(open(sys.argv[1])); print(sys.argv[1].split(\"/\")[-1]); print(\"keys\", sorted(d.keys())); [print(\"  \", k, \"=\", (d[k] if not isinstance(d[k], (dict, list)) else (type(d[k]).__name__, len(d[k])))) for k in sorted(d.keys())]; ev=d.get(\"events\") or d.get(\"history\") or []; [print(\"  ev\", e) for e in (ev[-40:] if isinstance(ev, list) else [])]" "$f"; done' 2>/dev/null
echo "=== wf_runs (fund runs · all)"
docker exec $PG psql -U updown -d updown -tAc "select symbol, leverage, margin_budget, seed_cash, opened_at, closed_at, closed_reason, left(meta_json::text, 160) from wf_runs where live order by opened_at" 2>/dev/null
echo "=== wf_trades (closed · all)"
docker exec $PG psql -U updown -d updown -tAc "select r.symbol, t.side, t.direction, t.outcome, t.opened_at, t.closed_at, t.entry_price, t.exit_price, t.qty, t.pnl, t.fee_actual, t.funding_paid, t.funding_pct, t.realized_adjust, t.actor from wf_trades t join wf_runs r on r.id=t.run_id where r.live order by t.opened_at" 2>/dev/null
echo "=== columns wf_trades"
docker exec $PG psql -U updown -d updown -tAc "select column_name from information_schema.columns where table_name='wf_trades' order by ordinal_position" 2>/dev/null | tr "\n" " "
echo
echo "=== event_logs resize/deposit/rebalance (last 60)"
docker exec $PG psql -U updown -d updown -tAc "select created_at, event_type, left(payload::text, 220) from event_logs where event_type ~ 'fund|rebalance|resize|deposit|withdraw|budget' order by created_at desc limit 60" 2>/dev/null
