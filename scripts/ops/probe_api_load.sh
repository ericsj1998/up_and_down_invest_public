#!/usr/bin/env bash
# 실계좌 api 가 무엇에 CPU 를 쓰나 — 최근 로그 끝에서만 읽는다(읽기 전용 · 2026-09-29 스틸 · 풀 고갈 조사).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_api_load.sh
#
# ⚠️ `docker logs --since` 는 로그 파일 전체를 훑어 스틸 상태에서 몇 분 걸린다 — `--tail` 만 쓴다.
API=updown_live-api-1
N=${N:-6000}
LOG=/tmp/api_tail.log
docker logs --tail "$N" "$API" > "$LOG" 2>&1
echo "== span"
grep -oE '"ts": "[^"]+"' "$LOG" | head -1
grep -oE '"ts": "[^"]+"' "$LOG" | tail -1
echo "== event types (top 25)"
grep -oE '"event_type": "[^"]{0,90}' "$LOG" | sed -E 's/HTTP Request: (GET|POST|DELETE) https:\/\/[^/]+\/api\/v4\/futures\/usdt\/([a-z_]+).*/HTTP \1 \2/' | sort | uniq -c | sort -rn | head -25
echo "== preview_sweep_slow"
grep '"preview_sweep_slow"' "$LOG" | tail -3 | cut -c1-300
echo "== live_step_slow"
grep '"live_step_slow"' "$LOG" | tail -3 | cut -c1-300
echo "== missed jobs"
grep -oE 'Run time of job \\"[^\\]+' "$LOG" | sort | uniq -c | sort -rn | head -8
echo "== stops / exits / panics (tail window)"
grep -E '"(live_runner_panic_close|live_stop_already_through|live_runner_stop_guard_failed|stop_missing|live_reconciled|live_signal_exit_failed|live_margin_exhausted|live_breaker_tripped)"' "$LOG" | tail -8 | cut -c1-260
rm -f "$LOG"
