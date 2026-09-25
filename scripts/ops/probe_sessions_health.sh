#!/usr/bin/env bash
# 실계좌 판(세션) 건강 — 최근 자가 점검 코드 · 오류급 사건 · 걸음 지연 · 락 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_sessions_health.sh
SINCE="15m"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · since $SINCE · 지금 $(date -u +%H:%M:%S)"
echo "=== 자가 점검 코드"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -10
echo "=== 오류 · 경고급 사건"
docker logs --since "$SINCE" "$API" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[a-z_]+"' | sort | uniq -c | sort -rn | head -12
echo "=== 판 걸음(최근 5분 · 판 수)"
docker logs --since 5m "$API" 2>&1 | grep -oE '"event_type": "(live_step[a-z_]*|live_walk[a-z_]*)"' | sort | uniq -c | head -6
echo "=== 락"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E 'trader_lock_lost|trader_promoted' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"' | paste - - | tail -3
echo "=== 메모리"
free -m | head -3
