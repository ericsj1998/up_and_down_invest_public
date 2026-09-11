#!/usr/bin/env bash
# 유니버스 봉 예열 진행 — 리더 api 의 stored_candles_filled(15m/1h/4h) 수 · 마지막 종목 · 실패 · 토스 429
set -u
for c in updown_live-api-1 updown_live-api_b-1; do
  docker ps --format '{{.Names}}' | grep -qx "$c" || continue
  echo "=== $c"
  docker logs --since 3h "$c" 2>&1 | grep stored_candles_filled | grep -oE '"frame": "(15m|1h|4h)"' | sort | uniq -c
  docker logs --since 3h "$c" 2>&1 | grep stored_candles_filled | grep -E '"frame": "(15m|1h|4h)"' | tail -1 | grep -oE '"symbol": "[^"]+", "frame": "[^"]+"|"fetched": [0-9]+|"ts": "[^"]+"' | tr '\n' ' '; echo
  echo "--- warm 이벤트/실패/429"
  docker logs --since 3h "$c" 2>&1 | grep -E 'warm_candles|job_started.*toss-warm|"status": 429' | grep -oE '"event_type": "[^"]+"|status.: 429' | sort | uniq -c
done
