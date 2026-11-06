#!/usr/bin/env bash
# 오류 사건 내용 · 엔진 다리 상태(읽기만)
P=$(docker ps --format '{{.Names}}' | grep postgres | head -1)
docker exec "$P" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F " | " -c "select ts at time zone '"'"'Asia/Seoul'"'"', event_type, left(payload_json::text, 230) from event_logs where event_type in ('"'"'run_watch_alarm'"'"','"'"'run_revive_failed'"'"','"'"'universe_name_mismatch'"'"') and ts > now() - interval '"'"'24 hours'"'"' order by ts desc limit 14"' 2>&1 | cut -c1-290
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
echo "== 엔진 다리"
docker logs "$C" --since 15m 2>&1 | grep -E 'leg_engine|private_strategy|engine_started|engine_seed' | grep -v 'HTTP Request' | tail -6 | cut -c1-230
echo "== 마지막 걸음 · 후보"
docker logs "$C" --since 5m 2>&1 | grep -oE '"event_type": "(session_[a-z_]+|live_entry[a-z_]*|preview_found|live_runner_[a-z_]+)"' | sort | uniq -c | sort -rn | head -10 | cut -c1-70
