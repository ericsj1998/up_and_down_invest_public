#!/usr/bin/env bash
# 봉 메우기 · 외부 호출 실패의 모양 — 최근 로그 끝에서만 (읽기 전용 · 2026-09-29).
API=updown_live-api-1
N=${N:-6000}
LOG=/tmp/api_tail2.log
docker logs --tail "$N" "$API" > "$LOG" 2>&1
echo "== live_feed_backfilled (sample)"
grep '"live_feed_backfilled"' "$LOG" | tail -4 | cut -c1-360
echo "== backfilled by symbol/frame"
grep '"live_feed_backfilled"' "$LOG" | grep -oE '"(symbol|timeframe|frame)": "[^"]+"' | sort | uniq -c | sort -rn | head -12
echo "== candlesticks by interval"
grep -oE 'candlesticks\?contract=[A-Z_]+&interval=[0-9a-z]+' "$LOG" | sed -E 's/contract=[A-Z_]+&//' | sort | uniq -c | sort -rn
echo "== outbound_failed / retry (sample)"
grep -E '"outbound_(failed|retry)"' "$LOG" | tail -4 | cut -c1-320
rm -f "$LOG"
