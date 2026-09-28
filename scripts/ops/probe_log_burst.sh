#!/usr/bin/env bash
# 로그가 무엇으로 폭주하나 — 끝 N 줄의 시간 폭 · 종류 · JSON 아닌 줄 (읽기 전용 · 2026-09-29).
API=updown_live-api-1
N=${N:-3000}
LOG=/tmp/api_tail4.log
docker logs --tail "$N" "$API" > "$LOG" 2>&1
echo "== span of last $N lines"
grep -oE '"ts": "[^"]+"' "$LOG" | sed -n '1p;$p'
echo "== event types"
grep -oE '"event_type": "[^"]{0,70}' "$LOG" | sort | uniq -c | sort -rn | head -12
echo "== non-json lines"
grep -vc '"event_type"' "$LOG"
grep -v '"event_type"' "$LOG" | cut -c1-160 | sort | uniq -c | sort -rn | head -8
rm -f "$LOG"
