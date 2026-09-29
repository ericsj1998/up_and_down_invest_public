#!/usr/bin/env bash
# XRP 손절 발동 확인 (읽기 전용) — 거래소 포지션 · 조건부 · 최근 끝난 주문 · 러너 사건.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ)"
docker exec -i "$API" python3 - <<'PY' 2>&1 | grep -vE 'outbound_request|adapter_created'
import asyncio
from updown.marketdata.provider import MarketDataProvider
from updown.execution.gateway import order_adapter
from updown.common.domain.instrument import Market
async def main():
    q = MarketDataProvider().adapter_for(Market.GATE)
    o = order_adapter(q, user_id="probe")
    pos = [p for p in await o._trade.get_positions() if int(p.get("size", 0)) != 0]
    print("POSITIONS", [(p.get("contract"), p.get("size")) for p in pos])
    stops = [r for r in await o._trade.list_stops(None)]
    print("STOPS", [((r.get("initial") or {}).get("contract"), (r.get("initial") or {}).get("size"), (r.get("trigger") or {}).get("price")) for r in stops])
    rows = await o._trade.list_orders("XRP_USDT", status="finished")
    for r in rows[:4]:
        print("ORD", r.get("size"), r.get("fill_price"), r.get("finish_time"), r.get("finish_as"), r.get("text"))
asyncio.run(main())
PY
echo "== XRP 러너 사건(최근 3h)"
docker logs --since 3h "$API" 2>&1 | grep 'XRP_USDT' | grep -v 'HTTP Request' | grep -E 'stop|close|exit|reconcile|share|audit' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"note": "[^"]{0,120}|"reason": "[^"]{0,80}|"code": "[^"]+"|"pnl[a-z_]*": "?[-0-9.]+' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 14 | cut -c1-300
