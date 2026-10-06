#!/usr/bin/env bash
# ETC_USDT 판 걸음 멈춤 4 — ETC · LTC 의 live_feed_backfilled 한 줄씩 · 4h 웹소켓 사건(읽기 전용 · 값만).
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
for S in ETC_USDT LTC_USDT; do
  echo "== $S live_feed_backfilled 마지막 두 줄"
  docker logs --since 2h "$API" 2>&1 | grep 'live_feed_backfilled' | grep "\"$S\"" | tail -n 2 | cut -c1-500
done
echo "== 4h 웹소켓 · 스트림 사건(ETC · LTC · 30h)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep -E 'gate_ws_|live_stream' | grep -E 'ETC_USDT|LTC_USDT' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"|"timeframe": "[0-9a-z]+"|"(reason|error|code)": "?[^",}]{0,80}' | paste -sd' ' | sed 's/ 2026-/\n2026-/g' | tail -n 20
echo "== 4h 봉 마감 처리 사건 종류(전체 판 · 1h)"
docker logs --since 1h "$API" 2>&1 | grep -oE '"event_type": "(live_[a-z_]*bar[a-z_]*|live_[a-z_]*close[a-z_]*|live_[a-z_]*judge[a-z_]*|live_[a-z_]*step[a-z_]*)"' | sort | uniq -c | sort -rn | head
