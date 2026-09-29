#!/usr/bin/env bash
# 한 종목의 최근 진입 크기 근거 (읽기 전용) — 옛 리더(api-1) 로그에서 주문 · 체결 · 크기 사건 payload.
SYM=XRP_USDT
for API in updown_live-api-1 updown_live-api_b-1; do
  echo "== $API · $SYM 사건 종류(주문 · 체결 · 크기)"
  docker logs "$API" 2>&1 | grep "$SYM" | grep -v 'HTTP Request' | grep -oE '"event_type": "(live_order[a-z_]*|live_fill[a-z_]*|live_entry[a-z_]*|session_entry[a-z_]*|live_size[a-z_]*|fund_grant[a-z_]*|live_placed[a-z_]*)"' | sort | uniq -c | sort -rn | head -n 10
  echo "== $API · 마지막 진입 주문 payload"
  docker logs "$API" 2>&1 | grep "$SYM" | grep -v 'HTTP Request' | grep -E '"event_type": "(live_order_sent|live_order_placed|live_entry_sent|live_entry_filled|live_fill_absorbed|live_order_filled)"' | tail -n 2 | cut -c1-1400
  echo "== $API · 변동성 배수 · 문 사건"
  docker logs "$API" 2>&1 | grep "$SYM" | grep -E 'vol:|vol_mult|gate:fit|granted|exposure' | grep -v 'HTTP Request' | tail -n 3 | cut -c1-600
done
echo "== 계좌 총액(펀드 틱 · 최근 1)"
docker logs updown_live-api_b-1 2>&1 | grep -E '"event_type": "fund_tick' | tail -n 1 | grep -oE '"total": "[^"]+"|"balance": "[^"]+"|"equity": "[^"]+"|"slots": [0-9]+' | paste -sd' '
