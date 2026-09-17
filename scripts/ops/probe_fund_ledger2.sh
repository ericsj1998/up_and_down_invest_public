#!/usr/bin/env bash
# 라이브 펀드 원장 프로브 2 — twr 원장(입출금 흐름) · basket · 거래 전부 (읽기 전용 · 키 없음)
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --format "{{.Names}}" | grep -E "updown_live-api" | head -1)
PG=updown_live-postgres-1
echo "=== fund twr · basket · runs"
docker exec "$API" sh -c 'for f in /app/logs/funds/*.json; do [ -f "$f" ] && python -c "import json,sys; d=json.load(open(sys.argv[1])); t=d[\"twr\"]; print(\"twr:\", {k: (v if k != \"flows\" else len(v)) for k, v in t.items()}); [print(\"  flow\", x) for x in t.get(\"flows\", [])]; print(\"basket:\", json.dumps(d[\"basket\"], ensure_ascii=False)[:1500]); print(\"runs:\", json.dumps(d[\"runs\"], ensure_ascii=False)[:1500])" "$f"; done' 2>/dev/null
echo "=== wf_trades (live · all)"
docker exec $PG psql -U updown -d updown -tAc "select r.symbol, t.direction, t.outcome, t.opened_at, t.closed_at, t.entry, t.exit_price, t.planned_stop, t.leverage, t.cost_pct, t.fee_actual, t.funding_paid, t.funding_pct, t.realized_adjust, t.actor, left(t.note,80) from wf_trades t join wf_runs r on r.id=t.run_id where r.live order by t.opened_at" 2>/dev/null
echo "=== wf_trades count by run"
docker exec $PG psql -U updown -d updown -tAc "select r.symbol, r.opened_at::date, count(*) from wf_trades t join wf_runs r on r.id=t.run_id where r.live group by 1,2 order by 2" 2>/dev/null
echo "=== event_logs columns"
docker exec $PG psql -U updown -d updown -tAc "select column_name from information_schema.columns where table_name='event_logs' order by ordinal_position" 2>/dev/null | tr "\n" " "
echo
echo "=== event types (fund-ish · counts)"
docker exec $PG psql -U updown -d updown -tAc "select event_type, count(*) from event_logs where event_type ~ 'fund|rebal|resize|budget|twr|flow|deposit' group by 1 order by 2 desc limit 30" 2>/dev/null
