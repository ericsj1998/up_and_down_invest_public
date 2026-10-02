#!/usr/bin/env bash
# 라이브 판 봉 공급 정지 점검 (읽기 전용) — 시간대별 사건 수 · 스트림 끊김 · 백필 · 걸음 · BTC 판 사건 · 오류.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API"
echo "== 시간대별(UTC 시) 사건 수 — 걸음 · 백필 · 스트림 · 진입 판정"
docker logs --since 12h "$API" 2>&1 | grep -oE '"ts": "2026-10-0[23]T[0-9]{2}|"event_type": "(live_[a-z_]+|session_[a-z_]+|gate_ws_[a-z_]+|preview_found|live_feed_backfilled)' | paste - - | awk '{print $2, $4}' | sort | uniq -c | awk '{print $2, $3, $1}' | sort | awk '{a[$1]=a[$1] " " $2 "=" $3} END {for (k in a) print k, a[k]}' | sort | cut -c1-300
echo "== 스트림 · 피드 관련 줄(12시간)"
docker logs --since 12h "$API" 2>&1 | grep -E 'stream_closed|ws_error|reconnect|stale|feed_gap|subscribe|stalled' | grep -oE '"ts": "[^"]{19}|"event_type": "[a-z_]+"|"(reason|error|symbol|code)": "?[^",}]{0,60}' | paste -sd' ' | fold -w 250 | head -n 12
echo "== 오류 · 경고 종류(12시간)"
docker logs --since 12h "$API" 2>&1 | grep -E '"level": "(error|warning)"|\[(error|warning)' | grep -oE '"event_type": "[a-z_0-9]+|\] [a-z_]+' | sort | uniq -c | sort -rn | head -15
