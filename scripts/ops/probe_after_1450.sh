#!/usr/bin/env bash
# 1.45.0 배포 뒤 — 읽기만: 오류 사건 수 · 되살린 판 수 · DB 이전 · 예측 모형 폴더 · 막힌 후보 기록
set -u
cd ~/updown || exit 1
A=updown_live-api-1
echo "=== 사건 요약(기동 뒤)"
docker logs --since 15m "$A" 2>&1 | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -25
echo "=== level error 수"
docker logs --since 15m "$A" 2>&1 | grep -c '"level": "error"'
docker logs --since 15m "$A" 2>&1 | grep '"level": "error"' | cut -c1-220 | head -5
echo "=== 예측 모형 폴더"
docker exec "$A" sh -c 'ls /app/config/forecast 2>/dev/null | wc -l; ls -la /app/config/forecast 2>/dev/null | head -8'
echo "=== DB 이전 머리"
docker exec updown_live-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select version_num from alembic_version"' 2>&1 | tail -1
