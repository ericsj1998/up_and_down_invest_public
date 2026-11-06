#!/usr/bin/env bash
# 분기 우주 스냅샷 상태(읽기만) — 작업 사건과 표 행
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 2h 2>&1 | grep -E 'universe_snapshot|universe_job' | grep -v 'Running job\|executed successfully\|Added job' | tail -8 | cut -c1-260
P=$(docker ps --format '{{.Names}}' | grep postgres | head -1)
docker exec "$P" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select quarter, profile, status, created_at from universe_snapshots order by created_at desc limit 5"' 2>&1 | cut -c1-140
free -m | awk '/Mem:/{print "가용 "$7" MB"} /Swap:/{print "스왑 "$3" MB"}'
