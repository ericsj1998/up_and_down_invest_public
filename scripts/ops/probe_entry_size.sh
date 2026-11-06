#!/usr/bin/env bash
# 진입 크기 계산 흔적(읽기만 · 12 시간 로그)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 12h > /tmp/ev.txt 2>&1
grep -E '"event_type": "(live_runner_sizing|live_filled_exposure|live_entry_price_corrected)"' /tmp/ev.txt | grep -E 'NEAR_USDT|RIVER_USDT' | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | cut -c1-430
rm -f /tmp/ev.txt
