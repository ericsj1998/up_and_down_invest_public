#!/usr/bin/env bash
# 스틸 · 걸음 · 손절 상태 (읽기 전용) — 버스트 크레딧 소진 의심 때.
echo "== $(date -u +%H:%M:%SZ) vmstat 8초"; vmstat 2 4 | tail -n 3 | awk '{print "user="$13" sys="$14" idle="$15" steal="$17}'
echo "== 컨테이너 CPU"; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' 2>/dev/null | sort -k2 -r | head -n 6
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== 최근 10분 걸음 · 손절 · 대조 사건"
docker logs --since 10m "$API" 2>&1 | grep -oE '"event_type": "(live_step_slow|live_stop[a-z_]*|live_reconcil[a-z_]*|hold_after_unreadable|live_guard[a-z_]*|live_bar[a-z_]*|session_[a-z_]*)' | sort | uniq -c | sort -rn | head -n 10
echo "== live_step_slow 원문(최근 2)"
docker logs --since 10m "$API" 2>&1 | grep live_step_slow | tail -n 2 | cut -c1-260
echo "== nginx 5분 상태별"
docker logs --since 5m updown_live-web-1 2>&1 | grep -oE '" [0-9]{3} ' | sort | uniq -c | sort -rn | head -n 5
