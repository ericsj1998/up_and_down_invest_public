#!/usr/bin/env bash
# 펀드 세션 안쪽 — 걸음 실패 본문 · 걸음이 살아 있나(봉 채움 시각) · 호가 마름 종목. 이름·수·상태만 (시크릿 없음).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
SINCE=200m
echo "=== $API · $(date -u +%H:%M:%SZ)"
echo "=== 걸음 실패 본문"
docker logs --since $SINCE "$API" 2>&1 | grep "live_runner_step_failed" | tail -n 2 | cut -c1-700
echo "=== 호가 마름이 걸린 종목"
docker logs --since $SINCE "$API" 2>&1 | grep '"event_type": "live_book_dry"' | grep -oE '"symbol": "[A-Z_]+"' | sort | uniq -c
echo "=== 지금 호가가 마른 채인 판 (dry 뒤 wet 이 없는 것)"
docker logs --since $SINCE "$API" 2>&1 | grep -E '"event_type": "live_book_(dry|wet)"' | tail -n 4 | grep -oE '"symbol": "[A-Z_]+".*"event_type": "[a-z_]+"' | sed -E 's/"book".*"event_type"/ "event_type"/'
echo "=== 종목별 마지막 봉 채움 시각 (걸음이 살아 있나)"
docker logs --since 90m "$API" 2>&1 | grep '"event_type": "live_feed_backfilled"' | grep -oE '"symbol": "[A-Z_]+"|"ts": "[0-9T:-]+' | paste - - 2>/dev/null | awk '{last[$2]=$4} END {for (s in last) print s, last[s]}' | sort | head -20
echo "=== 느린 걸음 — 최대(ms)"
docker logs --since $SINCE "$API" 2>&1 | grep '"event_type": "live_step_slow"' | grep -oE '"max_ms": [0-9.]+' | sort -t' ' -k2 -n | tail -n 1
