#!/usr/bin/env bash
# 판 하나가 봉을 못 받고 멈췄을 때(frame_frozen · steps_stall) — 그 종목의 웹소켓 · 재연결 · 메우기 사건을 본다 (읽기만 · 2026-10-14 XLM).
#   SYM=XLM_USDT SINCE=2026-10-13T18:00:00 UNTIL=2026-10-14T02:00:00 bash scripts/ops/remote.sh scripts/ops/probe_run_freeze.sh
# remote.sh 는 환경 변수를 안 넘긴다 — 아래 기본값을 고쳐 올리거나 그대로 쓴다.
SYM="${SYM:-XLM_USDT}"
SINCE="${SINCE:-2026-10-13T18:00:00}"
UNTIL="${UNTIL:-2026-10-14T02:00:00}"
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
[ -n "$API" ] || { echo "도는 실계좌 API 없음"; exit 1; }
echo "api=$API · $SYM · $SINCE ~ $UNTIL (UTC)"
LOG=/tmp/probe_run_freeze.log
docker logs --since "$SINCE" --until "$UNTIL" "$API" > "$LOG" 2>&1
echo "로그 줄 $(wc -l < "$LOG")"
echo "--- 웹소켓 사건 종류(전체 종목)"
grep -oE '"event_type": "(gate_ws_[a-z_]+|live_runner_stream_[a-z_]+|live_runner_heal[a-z_]*|live_backfill[a-z_]*)"' "$LOG" | sort | uniq -c
echo "--- $SYM 이 든 웹소켓 · 스트림 사건(시각순 앞뒤 12줄)"
grep "$SYM" "$LOG" | grep -E 'gate_ws_|live_runner_stream|heal|backfill' | head -12 | cut -c1-260
grep "$SYM" "$LOG" | grep -E 'gate_ws_|live_runner_stream|heal|backfill' | tail -12 | cut -c1-260
echo "--- $SYM 사건 종류"
grep "$SYM" "$LOG" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -20
rm -f "$LOG"
