#!/usr/bin/env bash
# 리더 락 상실 시각 · 그 뒤 승격 시도 · 판 정지 상태 (읽기 전용)
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker logs "$API" > /tmp/ll.log 2>&1
echo "== 리더 사건 전부(시각)"
grep -E 'trader_lock_lost|trader_promoted|trader_follower|trader_leader|leader_retry|promotion|trader_stop|runs_paused|live_paused|trader_resume' /tmp/ll.log | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"note": "[^"]{0,100}"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 12
echo "== 락 상실 뒤 걸음 · 주문 · 손절 사건이 있나(최근 20분)"
docker logs --since 20m "$API" 2>&1 | grep -oE '"event_type": "(live_step[a-z_]*|live_order[a-z_]*|live_stop[a-z_]*|live_guard[a-z_]*|session_[a-z_]*|live_entry[a-z_]*|live_exit[a-z_]*|live_reconcil[a-z_]*|live_audit[a-z_]*)' | sort | uniq -c | sort -rn | head -n 10
echo "== 최근 감사 발견 코드"
docker logs --since 20m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -n 6
echo "== redis 락 키"
docker exec updown_live-redis-1 redis-cli GET updown:api:trader 2>&1 | head -n 2
docker exec updown_live-redis-1 redis-cli TTL updown:api:trader 2>&1 | head -n 1
rm -f /tmp/ll.log
