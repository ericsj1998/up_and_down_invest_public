#!/usr/bin/env bash
# H_USDT 매매가 목표 지정가로 닫힌 때(11-10 21:07 UTC) 옛 슬롯(api_b · 멈춘 컨테이너) 로그 — 어느 닫힘 경로였나(읽기만)
set -u
B=updown_live-api_b-1
SYM=${SYM:-H_USDT}
FROM=${FROM:-2026-11-10T20:55:00}
UNTIL=${UNTIL:-2026-11-10T21:30:00}
docker logs --since "$FROM" --until "$UNTIL" "$B" 2>&1 | grep "$SYM" | grep -vE 'HTTP Request|outbound_request|tickers|candles' | cut -c1-360 | head -40
echo "=== 이 창의 사건 수(H)"
docker logs --since "$FROM" --until "$UNTIL" "$B" 2>&1 | grep "$SYM" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -20
