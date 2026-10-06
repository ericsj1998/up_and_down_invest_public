#!/usr/bin/env bash
# ADA_USDT 10-06 00:00 ~ 01:30Z — 일봉 채널 진입이 왜 없었나(읽기 전용 · 값만).
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== ADA 사건(HTTP · 백필 뺌 · 10-05 23:50 ~ 10-06 01:30Z)"
docker logs --timestamps --since 22h "$API" 2>&1 | grep '"ADA_USDT"' | grep -v 'HTTP Request' | grep -v 'live_feed_backfilled' \
  | awk '$1 >= "2026-10-05T23:50" && $1 <= "2026-10-06T01:30"' | cut -c1-420 | head -n 30
echo "== ADA 지금 열린 원장(다리 · 진입 · 계약)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F'|' -c "select r.key, split_part(t.playbook,'@',1), to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), t.entry, t.contracts, t.outcome, r.closed_at is null from wf_trades t join wf_runs r on r.id=t.run_id where r.symbol='ADA_USDT' and r.live order by t.opened_at desc limit 6"
echo "== 진입 문 · 건너뜀 사건 종류(10-06 00 ~ 01Z · 전 판)"
docker logs --since 22h "$API" 2>&1 | grep -E 'gate_held|entry_gate|skip|blocked|fresh_close|live_entry' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head
