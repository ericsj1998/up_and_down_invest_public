#!/usr/bin/env bash
# 실계좌 펀드 저장본의 다리별 entry_limit (읽기 전용) — MACD 롱 24시간 1건이 저장본에 있나 · 최근 24h 이 다리 진입 수.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker exec "$API" sh -c 'for f in /app/logs/funds/*.json; do [ -f "$f" ] && python - "$f" <<EOF
import json, sys
d = json.load(open(sys.argv[1]))
print("펀드", d.get("label"), "| 묶음", d.get("playbook"), "| legs_revision", d.get("legs_revision"))
for leg in d.get("legs", []) or []:
    print("  다리", leg.get("playbook") or leg.get("key"), "| entry_limit", leg.get("entry_limit"), "| peer_open_max", leg.get("peer_open_max") or leg.get("entry_peer_open_max"), "| 종목", len(leg.get("symbols", []) or []))
EOF
done' 2>/dev/null
echo "=== 최근 24h MACD 롱 진입 · entry_limit 거절 로그"
docker logs --since 24h "$API" 2>&1 | grep -E 'private_strategy' | grep -E 'entered|live_entry|entry_limit|gate' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,50}|"symbol": "[^"]+"|"why": "[^"]{0,40}"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 8
docker logs --since 24h "$API" 2>&1 | grep -c '"entry_limit"'
