#!/usr/bin/env bash
# 매매법 전환 뒤 점검 (읽기 전용) — 손절 겹침 사건 · 조건부 주문의 표식(어느 판 것인지) · 밖으로 나간 호출 실패 · 감사 코드.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) $API"
echo "== 손절 관련 사건(20분)"
docker logs --since 20m "$API" 2>&1 | grep -E 'stop_dup|stop_replaced|stop_orphan|live_stop_placed|live_stop_cancel|live_stop_swept|leftover|sweep_leftovers|adopt' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"symbol": "[^"]+"|"note": "[^"]{0,140}|"count": [0-9]+|"kept": "[^"]+"|"dropped": \[[^]]{0,120}' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 16
echo "== 거래소 조건부 주문(표식 · 계약 · 발동가 · 생성 시각)"
docker exec -i "$API" python3 - <<'PY' 2>&1 | tail -n 12
import asyncio, os
from updown.marketdata.provider import MarketDataProvider
from updown.execution.gateway import order_adapter
from updown.common.domain.instrument import Market
async def main():
    q = MarketDataProvider().adapter_for(Market.GATE)
    o = order_adapter(q, user_id="probe")
    rows = await o._trade.list_stops(None)
    for r in rows:
        ini = r.get("initial") or {}
        print(r.get("id"), ini.get("contract"), ini.get("size"), (r.get("trigger") or {}).get("price"), r.get("create_time"), (ini.get("text") or r.get("text") or "")[:40])
asyncio.run(main())
PY
echo "== 밖으로 나간 호출 실패(30분)"
docker logs --since 30m "$API" 2>&1 | grep -E 'outbound_failed' | grep -oE "'venue': '[^']+'|'path': '[^']+'|'status': [0-9]+|'error': '[^']{0,80}" | paste -sd' ' | sed "s/'venue'/\n'venue'/g" | sort | uniq -c | head -n 6
echo "== 감사 코드(20분)"
docker logs --since 20m "$API" 2>&1 | grep 'live_audit_found' | grep -oE '"symbol": "[^"]+"|"code": "[^"]+"' | paste -sd' ' | sed 's/"symbol"/\n"symbol"/g' | sort | uniq -c | head -n 8
