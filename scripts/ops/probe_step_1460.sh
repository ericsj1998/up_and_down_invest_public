#!/usr/bin/env bash
# 1.46.0 배포 뒤 판정(걸음) 시간 — 읽기만: 느린 걸음(250 ms 넘음) 줄을 시각 · 걸린 ms · 판 p50 로 늘어놓는다.
#   bash scripts/ops/remote.sh scripts/ops/probe_step_1460.sh [since=40m]
set -u
cd ~/updown || exit 1
SINCE="${1:-40m}"
A=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
echo "=== $A · 최근 $SINCE"
L=$(docker logs --timestamps --since "$SINCE" "$A" 2>&1 | grep live_step_slow)
echo "live_step_slow 줄: $(printf '%s\n' "$L" | grep -c . )"
printf '%s\n' "$L" | grep -oE '^[0-9T:-]{16}|"took_ms": [0-9.]+|"p50_ms": [0-9.]+|"max_ms": [0-9.]+' | paste -sd' ' | sed 's/ 20[0-9][0-9]-/\n20&/g' | head -60
echo "=== 분 단위 묶음(시각 · 줄 수)"
printf '%s\n' "$L" | grep -oE '^[0-9T:-]{16}' | sort | uniq -c | tail -20
