#!/usr/bin/env bash
# 정지 해제 · 배포 뒤 확인 (읽기 전용) — 정지 값 · 리더 · 판 시작 사건(정지로 떴나 · 켜진 채 떴나) · 시세 구독 · 메모리.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) $API $(docker inspect -f '{{.Config.Image}}' "$API")"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select key, value, updated_at from app_settings where key='live_entries_halted'"
echo "== 사건 종류(20분)"
docker logs --since 20m "$API" 2>&1 | grep -oE '"event_type": "[a-z_0-9]+' | sort | uniq -c | sort -rn | grep -E 'leader|live_start|halt|resum|gate_ws|backfill|fund|error|failed|entry|order' | head -n 25
echo "== 메모리"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' | head
free -m | head -3
