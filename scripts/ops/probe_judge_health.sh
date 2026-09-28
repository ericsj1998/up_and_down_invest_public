#!/usr/bin/env bash
# 판정이 밀리나 · 로그 적재 실패로 보류된 행동이 있나 — 최근 로그 끝에서만 (읽기 전용 · 2026-09-29).
API=updown_live-api-1
N=${N:-8000}
LOG=/tmp/api_tail3.log
docker logs --tail "$N" "$API" > "$LOG" 2>&1
echo "== span"
grep -oE '"ts": "[^"]+"' "$LOG" | sed -n '1p;$p'
echo "== judge / frame / step problems"
grep -oE '"event_type": "(judgement_stalled|live_price_frame_behind|live_step_slow|live_runner_stop_guard_failed|live_audit_failed|live_audit_found|live_skipped_bar|live_runner_order_failed)"' "$LOG" | sort | uniq -c
echo "== AUDIT-LOG-FAILURE by event"
grep -oE 'AUDIT-LOG-FAILURE\] event_type=[A-Za-z_]+ [^ ]+ 행동=[^ ]+' "$LOG" | sed -E 's/trace_id=[^ ]+ //' | sort | uniq -c | sort -rn | head -10
echo "== entries / proposals"
grep -oE '"event_type": "(live_runner_sizing|live_entry_blocked|live_add_filled|preview_found)"' "$LOG" | sort | uniq -c
rm -f "$LOG"
