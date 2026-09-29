#!/usr/bin/env bash
# 자동 전환 뒤 XRP 새 세션의 사건 전부 13:39Z ~ 14:01Z (읽기 전용) — 입양 뒤 왜 진입 주문이 또 나갔나.
API=updown_live-api_b-1
docker logs "$API" 2>&1 | grep 'XRP_USDT' | grep -E '"ts": "2026-09-29T1(3:39|3:4[0-9]|3:5[0-9]|4:0[01])' | grep -v 'HTTP Request' | grep -vE 'live_feed_|preview|candles' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"run": "[^"]+"|"trade_id": "[^"]+"|"contracts": "?[0-9]+"?|"status": "[^"]+"|"outcome": "[^"]+"|"note": "[^"]{0,120}|"reason": "[^"]{0,100}|"kind": "[^"]+"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 40 | cut -c1-360
