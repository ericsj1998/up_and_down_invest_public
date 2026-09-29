#!/usr/bin/env bash
# ETH 손절 근거 (읽기 전용) — 원장 기록(다리 · 진입 · 손절) · 진입 사건 payload · 거래소 조건부.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== 원장(ETH 최근 3)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select t.playbook, t.actor, t.outcome, t.opened_at, t.entry, t.planned_stop, t.planned_first, t.planned_target, t.contracts from wf_trades t join wf_runs r on r.id = t.run_id where r.symbol='ETH_USDT' order by t.opened_at desc nulls last limit 3" 2>&1 | head -n 5
echo "== 진입 사건(13:58Z ~ 14:02Z · ETH)"
docker logs "$API" 2>&1 | grep 'ETH_USDT' | grep -E '"ts": "2026-09-29T1(3:5[89]|4:0[0-2])' | grep -v 'HTTP Request' | grep -E 'sizing|plan|entry|stop|setup|candidate|breakout' | grep -oE '"event_type": "[^"]+"|"stop": "[^"]+"|"planned_stop": "[^"]+"|"entry": "[^"]+"|"low": "[^"]+"|"bar_low": "[^"]+"|"stop_pct": "[^"]+"|"atr": "[^"]+"|"reason": "[^"]{0,80}|"book": "[^"]+"|"kind": "[^"]+"|"guard": "[^"]+"' | paste -sd' ' | cut -c1-900
echo "== 거래소 조건부(ETH)"
docker exec -i "$API" python3 - <<'PY' 2>&1 | grep -vE 'outbound_request|adapter_created'
import asyncio
from updown.marketdata.provider import MarketDataProvider
from updown.execution.gateway import order_adapter
from updown.common.domain.instrument import Market
async def main():
    q = MarketDataProvider().adapter_for(Market.GATE)
    o = order_adapter(q, user_id="probe")
    for r in await o._trade.list_stops(None):
        ini = r.get("initial") or {}
        if ini.get("contract") == "ETH_USDT":
            print("STOP", ini.get("size"), (r.get("trigger") or {}).get("price"), ini.get("text"), "reduce_only", ini.get("reduce_only"))
    rows = await o._trade.list_orders("ETH_USDT", status="finished")
    for r in rows[:2]:
        print("ORD", r.get("size"), r.get("fill_price"), r.get("finish_time"), r.get("text"))
asyncio.run(main())
PY
