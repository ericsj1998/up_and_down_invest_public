#!/usr/bin/env bash
# DB 연결이 누구에게 잡혀 있나 — 읽기 전용 (2026-09-29 · 실계좌 api QueuePool 고갈 조사).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_db_pool.sh
#
# pg_stat_activity 를 postgres 컨테이너 안 psql 로 읽는다. 값 · 비밀은 안 찍는다(쿼리 앞 120자만).
PG=updown_live-postgres-1
q() { docker exec -i "$PG" sh -c 'psql -X -A -F " | " -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$0"' "$1"; }

echo "== connections by db / state"
q "select datname, application_name, state, count(*) from pg_stat_activity where backend_type = 'client backend' group by 1,2,3 order by 4 desc;"
echo "== oldest non-idle (state, age, wait, query head)"
q "select pid, datname, state, now() - xact_start as xact_age, now() - query_start as query_age, wait_event_type, wait_event, left(regexp_replace(query, '\s+', ' ', 'g'), 120) from pg_stat_activity where backend_type = 'client backend' and state <> 'idle' order by xact_start nulls last limit 15;"
echo "== locks waiting"
q "select count(*) as waiting_locks from pg_locks where not granted;"
echo "== max_connections"
q "show max_connections;"
