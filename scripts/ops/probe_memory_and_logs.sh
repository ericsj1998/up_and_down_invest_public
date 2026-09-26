#!/usr/bin/env bash
# 서버 메모리 · 로그 실측 — 컨테이너별 메모리 · 한도 · 스왑 · 상위 프로세스 · 로그 종류별 크기(디스크) · DB 표 크기 (읽기만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_memory_and_logs.sh
#
# 왜(2026-09-26): 사용자 "램을 더 최적화할 수 없나 · 로그를 로컬에 저장하고 라이브 로그는 지울 수 있는 기능" — 무엇이 얼마를 쓰는지 먼저 잰다.
PG=updown_live-postgres-1
echo "=== 지금 $(date -u +%m-%dT%H:%M:%S) · 가동 $(uptime -p)"
echo "=== 메모리 · 스왑(MB)"
free -m
echo "=== 컨테이너별 메모리(사용 / 한도 · %)"
docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" | sort
echo "=== 상위 프로세스 RSS(MB)"
ps -eo rss,comm --sort=-rss | head -12 | awk 'NR==1{print; next}{printf "%6.0f %s\n", $1/1024, $2}'
echo "=== 스왑을 쓰는 프로세스(MB)"
for p in /proc/[0-9]*; do s=$(awk '/VmSwap/{print $2}' "$p/status" 2>/dev/null); [ -n "$s" ] && [ "$s" -gt 10240 ] && echo "$((s/1024)) $(cat "$p/comm" 2>/dev/null)"; done | sort -rn | head -8
echo "=== 디스크"
df -h / | tail -1
echo "=== Docker 컨테이너 로그(json-file · 회전 포함)"
sudo -n sh -c 'for d in /var/lib/docker/containers/*; do n=$(docker inspect -f "{{.Name}}" $(basename $d) 2>/dev/null); s=$(du -sm $d/*-json.log* 2>/dev/null | awk "{t+=\$1} END {print t+0}"); echo "$s MB $n"; done' 2>/dev/null | sort -rn | head -12 || echo "(sudo 없음)"
echo "=== Docker 볼륨 크기(MB)"
sudo -n sh -c 'du -sm /var/lib/docker/volumes/*/_data 2>/dev/null' | sort -rn | head -12 | sed 's#/var/lib/docker/volumes/##; s#/_data##'
echo "=== 앱 파일 로그(runlogs 안 · 상위)"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker exec "$API" sh -c 'du -sm /app/runlogs/* 2>/dev/null | sort -rn | head -12; echo "--- app 회전 파일"; ls -la /app/runlogs/app 2>/dev/null | tail -8' 2>/dev/null
echo "=== journald"
journalctl --disk-usage 2>/dev/null
echo "=== DB 표 크기 상위 · event_logs 행 수 · 가장 옛 행"
docker exec $PG psql -U updown -d updown -Atc "select relname, pg_size_pretty(pg_total_relation_size(relid)) from pg_catalog.pg_statio_user_tables order by pg_total_relation_size(relid) desc limit 10"
docker exec $PG psql -U updown -d updown -Atc "select count(*), min(ts), max(ts) from event_logs"
docker exec $PG psql -U updown -d updown -Atc "select pg_size_pretty(pg_database_size('updown'))"
echo "=== event_logs 권한(append-only 확인)"
docker exec $PG psql -U updown -d updown -Atc "select grantee, privilege_type from information_schema.role_table_grants where table_name='event_logs' order by 1,2"
