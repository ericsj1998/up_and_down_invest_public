#!/usr/bin/env bash
# T218 — 대기 진입 계획이 DB 에 남아 있나 (배포 뒤 이어받을 근거). 열린 판마다 표 수를 본다.
cd ~/updown 2>/dev/null || exit 1
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select symbol, coalesce(jsonb_array_length(meta_json->'pending_entry'->'tickets'), 0) as tickets, meta_json->'pending_entry'->'record'->>'trade_id' as trade from wf_runs where closed_at is null order by symbol"
API=$(docker ps --format "{{.Names}}" | grep -E "updown_live-api" | head -1)
echo "=== pending events (30m)"; docker logs --since 30m "$API" 2>&1 | grep -oE '"event_type": "(live_pending_[a-z_]+|leftover_zombie_entry_swept|live_limit_entry_failed)"' | sort | uniq -c
