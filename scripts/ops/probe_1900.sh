#!/usr/bin/env bash
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== 18:59:30 ~ 19:00:30Z 이벤트 종류"; docker logs --tail 80000 "$API" 2>&1 | grep -E '"ts": "2026-09-28T(18:59:[3-5]|19:00:[0-3])' | grep -oE '"event_type": "[^"]{0,70}' | sort | uniq -c | sort -rn | head -15
echo "== legs_revision · fund_ 이벤트 18:30Z 이후"; docker logs --tail 80000 "$API" 2>&1 | grep -E '"ts": "2026-09-28T(18:[3-5]|19:)' | grep -E 'legs_revision|fund_' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"note": "[^"]{0,80}' | paste - - - | tail -8
echo "== 정지 끝났나(trader_stop · stopped · runner_stopped 18:00Z 이후)"; docker logs --tail 80000 "$API" 2>&1 | grep -E '"ts": "2026-09-28T(1[89]|2[0-3]):' | grep -E 'live_runner_stopped|live_runners_stopped|trader_stopped|autostart|api_stopped|engine_stop' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"' | paste - - | tail -5
