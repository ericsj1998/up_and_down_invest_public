#!/usr/bin/env bash
# 펀드 생성 POST 503 의 이유 — 그 요청의 guard 로그(리더/https/기능) · 리더 상태 (값 없음).
cd ~/updown 2>/dev/null || exit 1
A=updown_live-api_b-1
echo "== 503 근처 guard/leader 로그 (17:34)"
docker logs --since 3h $A 2>&1 | grep -E '17:34:(2[5-9]|3[0-5])' | grep -oE '"event_type": "[^"]{0,100}"|"detail": "[^"]{0,160}"|"reason": "[^"]{0,120}"' | head -12
echo "== 503 을 내는 이벤트 종류 (3h · 전체)"
docker logs --since 3h $A 2>&1 | grep -iE 'not_leader|follower|guard_|https|leader' | grep -oE '"event_type": "[^"]{0,80}"' | sort | uniq -c | sort -rn | head -8
echo "== 리더 상태"
docker exec $A python -c "
import urllib.request, json
print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health')).get('leader'))
" 2>&1 | tail -2
docker exec $A sh -c 'echo "LIVE_ORDERS set: $([ -n "$LIVE_ORDERS" ] && echo yes || echo no)"'
