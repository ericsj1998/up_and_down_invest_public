#!/usr/bin/env bash
# 계약 승수 — 정수 계약을 백테스트에 넣으려면 종목마다 "계약 1개 = 코인 몇 개" 를 알아야 한다.
set -uo pipefail
API=$(docker ps --filter "name=api_b" --filter "status=running" --format "{{.Names}}" | head -1)
[ -n "$API" ] || API=$(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}" | head -1)
docker exec -i "$API" python - <<'PY'
import asyncio
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SYMS = ["BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT"]


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    print("MULT = {  # Gate 실측 " + __import__("datetime").date.today().isoformat())
    for sym in SYMS:
        s = await c.contract(sym)
        mult = s["quanto_multiplier"]
        px = s["last_price"]
        print(f'    "{sym}": Decimal("{mult}"),  # 가격 {px} → 계약당 명목 {float(mult) * float(px):.2f}')
    print("}")


asyncio.run(main())
PY
