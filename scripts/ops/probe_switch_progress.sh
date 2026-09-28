#!/usr/bin/env bash
# 전환 진행 한 줄 — 17:20Z 이후 새로 뜬 판 수 · 리더 락 TTL · 스틸 (읽기 전용 · 2026-09-29).
n=$(docker logs --tail 40000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "live_session_started"' | grep -cE '"ts": "2026-09-28T1[7-9]:(2[0-9]|[3-5][0-9])|"ts": "2026-09-28T(1[89]|2[0-3]):|"ts": "2026-09-29T')
last=$(docker logs --tail 40000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "live_session_started"' | tail -1 | grep -oE '"symbol": "[^"]+"|"ts": "[^"]+"' | paste -sd' ')
ttl=$(docker exec updown_live-redis-1 redis-cli ttl updown:api:trader 2>/dev/null)
st=$(top -bn1 | sed -n 3p | grep -oE '[0-9.]+ st')
fail=$(docker logs --tail 40000 updown_live-api_b-1 2>&1 | grep -cE '전략 전환 실패|fund_switch_failed')
echo "$(date -u +%H:%MZ) 새 판 $n/40 · 마지막 $last · 리더 TTL $ttl · 스틸 $st · 전환 실패 로그 $fail"
