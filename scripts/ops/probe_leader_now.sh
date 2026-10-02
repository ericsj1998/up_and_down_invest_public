#!/usr/bin/env bash
# 거래 리더 상태 (읽기 전용) — 리더 · 팔로워 사건 시각 · 지금 리더 키 · 컨테이너 상태 · 화면 봉 출처.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API"
docker logs --since 20h "$API" 2>&1 | grep -E 'trader_(leader|follower)|leader_lost|lock_lost|leader' | grep -oE '"ts": "[^"]{19}|"event_type": "[a-z_]+"|"(reason|why|holder|owner)": "?[^",}]{0,60}' | paste - - | tail -n 10
echo "== 리더 락(redis · 값은 길이만)"
R=$(docker ps --format '{{.Names}}' | grep -E 'redis' | head -1)
docker exec "$R" sh -c 'for k in $(redis-cli --scan --pattern "*leader*" | head -5); do echo "$k ttl=$(redis-cli pttl $k)ms"; done'
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api|web'
