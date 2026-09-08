#!/usr/bin/env bash
# 00:00Z NEAR 3계약 감축(reduce-only · taker) 이 무엇이었나 — 그때 리더 api-1 의 비-HTTP 이벤트
set -u
echo "=== api-1 2026-09-07T23:59:30 ~ 00:01:30Z (HTTP 제외)"
docker logs --since 2026-09-07T23:59:30Z --until 2026-09-08T00:01:30Z updown_live-api-1 2>&1 | grep -v "HTTP Request" | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"symbol": "[A-Z_]+"|"size": [^,}]+|"reason": "[^"]{0,80}"|"note": "[^"]{0,100}"|"contracts": [^,}]+|"target": [^,}]+' | paste -d' ' - - - - | head -40
echo "=== api-1 resize/reduce 계열 이벤트 (48h)"
docker logs --since 48h updown_live-api-1 2>&1 | grep -oE '"event_type": "[a-z_]*(resize|reduce|trim|shrink|rebalance|weight|relever)[a-z_]*"' | sort | uniq -c | head -12
echo "=== api_b resize/reduce 계열 이벤트 (12h)"
docker logs --since 12h updown_live-api_b-1 2>&1 | grep -oE '"event_type": "[a-z_]*(resize|reduce|trim|shrink|rebalance|weight|relever)[a-z_]*"' | sort | uniq -c | head -12
