#!/usr/bin/env bash
# 1.46.0 배포 뒤 — 읽기만: 떠 있는 컨테이너 · 오류 수 · 느린 걸음 · 호출 계량 줄 · 익절 거절 · 남은 손절 거두기 · 되살린 판 수
set -u
cd ~/updown || exit 1
SINCE="${1:-20m}"
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep -E 'api|web' | head -6
for A in updown_live-api-1 updown_live-api_b-1; do
  docker ps --format '{{.Names}}' | grep -qx "$A" || continue
  echo "=== $A (최근 $SINCE)"
  L=$(docker logs --since "$SINCE" "$A" 2>&1)
  echo "level error: $(printf '%s\n' "$L" | grep -c '"level": "error"')"
  printf '%s\n' "$L" | grep '"level": "error"' | cut -c1-200 | head -4
  echo "사건 상위:"
  printf '%s\n' "$L" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -14
  echo "live_step_slow: $(printf '%s\n' "$L" | grep -c live_step_slow)"
  printf '%s\n' "$L" | grep live_step_slow | grep -oE '"took_ms": [0-9.]+' | sort -t: -k2 -n | tail -3
  echo "ratelimit_paths 줄: $(printf '%s\n' "$L" | grep -c ratelimit_paths) · ratelimit_close: $(printf '%s\n' "$L" | grep -c ratelimit_close)"
  printf '%s\n' "$L" | grep ratelimit_paths | tail -1 | cut -c1-400
  echo "REDUCE_ONLY_FAIL: $(printf '%s\n' "$L" | grep -c REDUCE_ONLY_FAIL) · share_target_failed: $(printf '%s\n' "$L" | grep -c live_share_target_failed)"
  echo "되살린 판: $(printf '%s\n' "$L" | grep -cE 'live_restored|run_restored|restored_run')"
done
