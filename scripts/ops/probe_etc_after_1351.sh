#!/usr/bin/env bash
# 1.35.1 뒤 ETC 판이 4H 마감에 걸었나 · 감시 경보 · 대기 주문(읽기 전용 · 값만 · 2026-10-07).
#   bash scripts/ops/remote.sh scripts/ops/probe_etc_after_1351.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API"
echo "== 감시 경보(기동 뒤 · 코드 · 종목)"
docker logs --since 6h "$API" 2>&1 | grep 'run_watch_alarm' | grep -oE '"code": "[a-z_]+"|"detail": "[A-Z0-9_]+' | paste - - | sort | uniq -c
echo "== ETC 사건(기동 뒤 · 걸음 · 스트림 · 감사)"
docker logs --timestamps --since 6h "$API" 2>&1 | grep '"ETC_USDT"' | grep -v 'HTTP Request' | grep -oE '^[0-9T:-]{16}|"event_type": "[a-z_]+"' | paste - - | awk '{print $1, $3}' | sort | uniq -c | grep -v live_feed_backfilled | tail -n 12
echo "== live_step_slow · 판정 사건(ETC · 12:00Z · 16:00Z 마감 근처)"
docker logs --timestamps --since 6h "$API" 2>&1 | grep '"ETC_USDT"' | grep -E 'live_step_slow|session_|live_runner_sizing' | cut -c1-200 | tail -n 5
echo "== 열린 매매 · 대기(전 판)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F'|' -c "select r.symbol, split_part(t.playbook,'@',1), t.outcome, to_char(coalesce(t.opened_at,t.placed_at) at time zone 'UTC','MM-DD HH24:MI') from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.closed_at is null and t.closed_at is null order by 4"
