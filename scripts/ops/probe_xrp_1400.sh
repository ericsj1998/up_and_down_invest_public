#!/usr/bin/env bash
# 14:00Z 앞뒤 XRP 사건 전부 (읽기 전용) — 왜 51계약이 더 체결됐나.
API=updown_live-api_b-1
echo "== 13:55Z ~ 14:05Z XRP 사건(HTTP 제외)"
docker logs "$API" 2>&1 | grep 'XRP_USDT' | grep -E '"ts": "2026-09-29T1(3:5[5-9]|4:0[0-5])' | grep -v 'HTTP Request' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"note": "[^"]{0,160}|"contracts": "[^"]+"|"intended": "[^"]{0,8}|"trade_id": "[^"]+"|"reason": "[^"]{0,120}|"kind": "[^"]+"|"side": "[^"]+"|"size": "?-?[0-9.]+"?' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 40 | cut -c1-400
echo "== 거래소 XRP 포지션 · 최근 끝난 주문 5"
docker exec -i "$API" python3 - <<'PY' 2>&1 | grep -vE 'outbound_request|adapter_created'
import asyncio
from updown.marketdata.provider import MarketDataProvider
from updown.execution.gateway import order_adapter
from updown.common.domain.instrument import Market
async def main():
    q = MarketDataProvider().adapter_for(Market.GATE)
    o = order_adapter(q, user_id="probe")
    pos = await o._trade.get_positions()
    for p in pos:
        if p.get("contract") == "XRP_USDT":
            print("POS", p.get("contract"), p.get("size"), p.get("entry_price"), p.get("leverage"), p.get("margin"))
    rows = await o._trade.list_orders("XRP_USDT", status="finished")
    for r in rows[:6]:
        print("ORD", r.get("id"), r.get("size"), r.get("left"), r.get("fill_price"), r.get("finish_as"), r.get("finish_time"), r.get("text"))
asyncio.run(main())
PY
