#!/usr/bin/env bash
# 펀드·판 점검 — 판이 몇 개 열렸고, 거래소와 맞는가, 러너 자가 점검에 걸린 것이 있나.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)  # 리더 실계좌 api 만 — api_demo(테스트넷)를 먼저 집던 결함(2026-09-24)
echo "=== open runs"; docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select symbol, leverage, margin_budget, opened_at::time from wf_runs where closed_at is null order by symbol"
echo "=== budget sum"; docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select coalesce(sum(margin_budget),0) from wf_runs where closed_at is null"
echo "=== recently closed (6)"; docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select symbol, closed_reason, closed_at::time from wf_runs where closed_at is not null order by closed_at desc limit 6"
echo "=== fund records"; docker exec "$API" sh -c 'for f in /app/logs/funds/*.json; do [ -f "$f" ] && python -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get(\"label\"), \"|\", d.get(\"weight_mode\"), \"| lev\", d.get(\"leverage\"), \"| alt\", d.get(\"alt_leverage\"), \"|\", sorted(d.get(\"handles\",{}).keys()))" "$f"; done' 2>/dev/null
echo "=== runner lifecycle events (60m)"; docker logs --since 60m "$API" 2>&1 | grep -oE '"event_type": "(wf_run_opened|live_runner_started|live_leverage_synced|gate_ws_subscribed|live_run_resumed|live_underfunded|live_close_failed|live_adopt_unreadable|orphan[a-z_]*)"' | sort | uniq -c
echo "=== audit codes (10m)"; docker logs --since 10m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
echo "=== trades (DB · last 5)"; docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, t.side, t.opened_at::time, t.closed_at::time, t.pnl from wf_trades t join wf_runs r on r.id=t.run_id order by t.opened_at desc limit 5" 2>/dev/null
