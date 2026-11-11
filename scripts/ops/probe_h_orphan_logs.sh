#!/usr/bin/env bash
# H_USDT 고아 손절이 배포 뒤 사라짐 — 누가 지웠나(api 로그 · 읽기만) · 그리고 1.44.1 때(지난 슬롯 로그는 없음) 목표 체결 처리 사건
set -u
A=$(docker ps --filter name=updown_live-api --filter status=running --format '{{.Names}}' | head -1)
SYM=${SYM:-H_USDT}
docker logs --since 60m "$A" 2>&1 | grep "$SYM" | grep -vE 'HTTP Request|outbound_request|tickers' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -20
echo "=== 잔재 · 손절 취소 · 목표 사건 본문(앞 300)"
docker logs --since 60m "$A" 2>&1 | grep "$SYM" | grep -E 'leftover|cancel|stop_|share_target|orphan|swept|naked' | cut -c1-300 | head -12
