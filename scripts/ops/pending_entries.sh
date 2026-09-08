#!/usr/bin/env bash
# 대기 중 진입 지정가가 왜 안 채워지나 — 주문가 vs 지금 시세, 러너가 그 주문을 어떻게 다루는지(로그).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --format "{{.Names}}" | grep -E "updown_live-api" | head -1)
echo "=== now $(date -u +%FT%TZ) (KST $(TZ=Asia/Seoul date +%H:%M))"
echo "=== mark vs pending order price"
for c in BTC_USDT ETH_USDT; do
  curl -s "https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=$c" | python3 -c "import json,sys; t=json.load(sys.stdin)[0]; print('$c', 'last=', t['last'], 'mark=', t['mark_price'], 'bid=', t.get('highest_bid'), 'ask=', t.get('lowest_ask'))"
done
echo "=== entry/maker events for these runs (90m)"
docker logs --since 90m "$API" 2>&1 | grep -E "livec1bd2c91|live9da01125|bd2c91|a01125" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -14
echo "=== the order lifecycle lines (last 12 · 요약)"
docker logs --since 90m "$API" 2>&1 | grep -E '"event_type": "(live_entry[a-z_]*|entry_[a-z_]*|maker_[a-z_]*|order_(placed|amended|cancel[a-z]*|replaced|expired|filled)[a-z_]*|plan_[a-z_]*|live_order[a-z_]*)"' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"symbol": "[A-Z_]+"|"price": "[^"]+"|"reason": "[^"]{0,80}|"note": "[^"]{0,100}' | paste - - - - 2>/dev/null | tail -12 | cut -c1-230
echo "=== last audit codes"
docker logs --since 10m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c
