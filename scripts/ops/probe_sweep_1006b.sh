#!/usr/bin/env bash
# 실계좌 한 바퀴 교차 점검 2 (읽기 전용 · 값만 · 2026-10-06) — 1 에서 걸린 사건들의 본문.
#   bash scripts/ops/remote.sh scripts/ops/probe_sweep_1006b.sh
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
LOG=/tmp/sweep_api_b.log
docker logs --timestamps --since 26h "$API" > "$LOG" 2>&1
pick() { grep -oE "\"($1)\": \"?[^\",}]{0,$2}" ; }

echo "=== live_step_slow — 종목별 수 · 걸린 초(최대)"
grep 'live_step_slow' "$LOG" | head -n 2 | cut -c1-600
grep 'live_step_slow' "$LOG" | grep -oE '"symbol": "[A-Z0-9_]+"' | sort | uniq -c | sort -rn | head -n 12
grep 'live_step_slow' "$LOG" | grep -oE '"(seconds|elapsed_s|took_s|elapsed)": [0-9.]+' | awk -F': ' '{print int($2)}' | sort -n | awk '{a[NR]=$1} END {print "n", NR, "중앙", a[int(NR/2)+1], "최대", a[NR]}'
echo "=== live_step_slow 시각 분포(UTC 시:분 끝자리)"
grep 'live_step_slow' "$LOG" | cut -c12-16 | sort | uniq -c | sort -rn | head -n 8

echo "=== live_fee_align_skipped — 본문 둘 · 이유별 수"
grep 'live_fee_align_skipped' "$LOG" | head -n 2 | cut -c1-500
grep 'live_fee_align_skipped' "$LOG" | grep -oE '"(reason|why|note)": "[^"]{0,80}' | sort | uniq -c | sort -rn | head -n 6

echo "=== session_entry_ref_volpct_held · 진입 문 보류 전부(26h)"
grep -E 'session_entry_[a-z_]*held|session_entry_gate' "$LOG" | cut -c1-700

echo "=== 스트림 사건(watcher · opened · first · subscribed · closed · error) — 26h 종목 · 축"
grep -E '"event_type": "(live_stream_[a-z_]+|gate_ws_[a-z_]+)"' "$LOG" | grep -oE '^[0-9T:-]{16}|"event_type": "[a-z_]+"|"symbol": "[A-Z0-9_]+"|"(timeframe|frame|frames)": "?[^",}]{0,40}|"(reason|error|note)": "[^"]{0,70}' | paste -sd' ' | sed 's/ 2026-/\n2026-/g' | tail -n 40

echo "=== live_audit_found 본문(26h)"
grep 'live_audit_found' "$LOG" | cut -c1-600

echo "=== live_book_dry 본문"
grep 'live_book_dry' "$LOG" | head -n 3 | cut -c1-500

echo "=== preview_sweep_slow · trader_follower · fund_brake_reset · fund_legs_refreshed"
grep -E 'preview_sweep_slow|trader_follower|fund_brake_reset|fund_legs_refreshed' "$LOG" | cut -c1-500

echo "=== api_memory_beat 처음 · 마지막 셋"
grep 'api_memory_beat' "$LOG" | sed -n '1p' | cut -c1-400
grep 'api_memory_beat' "$LOG" | tail -n 3 | cut -c1-400

echo "=== 1d 조회 시각(전 종목 · 10-05 23:00 ~ 10-06 02:30Z · 분 단위 묶음)"
grep 'interval=1d' "$LOG" | awk '$1 >= "2026-10-05T23:00" && $1 <= "2026-10-06T02:30"' | cut -c1-16 | sort | uniq -c | head -n 20
echo "=== 4h 조회 시각(10-06 08:00 ~ 08:30Z · 분 단위 묶음 — 1h 판의 4h 판정 축)"
grep 'interval=4h' "$LOG" | awk '$1 >= "2026-10-06T07:55" && $1 <= "2026-10-06T08:40"' | cut -c1-16 | sort | uniq -c | head -n 20
rm -f "$LOG"
