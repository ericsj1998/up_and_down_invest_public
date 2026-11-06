#!/usr/bin/env bash
# 나스닥 엔진 다리 상태 · 코인 판정 흔적(읽기만)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker exec "$C" sh -c 'ls -la logs/funds/engines 2>/dev/null | tail -3; for f in logs/funds/engines/*; do [ -f "$f" ] && { echo "$f"; head -c 500 "$f"; echo; }; done' 2>/dev/null | cut -c1-520
docker logs "$C" --since 70m 2>&1 > /tmp/ev.txt; docker logs "$C" --since 70m > /tmp/ev.txt 2>&1
grep -E 'index_leg|private_strategy' /tmp/ev.txt | grep -v 'HTTP Request' | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | tail -5 | cut -c1-240
echo "== 코인 판정 흔적(70 분)"
grep -oE '"event_type": "(session_[a-z_]+|live_entry[a-z_]*|entry_[a-z_]+|preview_found|blocked_[a-z_]+|candidate[a-z_]*|live_gate[a-z_]*|live_candidate[a-z_]*)"' /tmp/ev.txt | sort | uniq -c | sort -rn | head -12 | cut -c1-70
rm -f /tmp/ev.txt
