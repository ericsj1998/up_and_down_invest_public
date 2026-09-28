#!/usr/bin/env bash
# T320 P0 탐침 종목(기본 SUI_USDT · Gate 테스트넷 데모 계정)의 **지금 상태만** 읽는다 — 포지션 · 미결 · 조건부. 주문은 안 낸다.
#
#     bash scripts/ops/remote.sh scripts/ops/probe_shared_position_state.sh
set -u
DEMO=$(docker ps --format '{{.Names}}' | grep -E 'api_demo' | head -1)
[ -n "$DEMO" ] || { echo "api_demo 컨테이너가 없다"; exit 1; }
docker exec -i -e PROBE_SYMBOL="${1:-SUI_USDT}" "$DEMO" python - <<'PY' 2>&1 | grep -E '^P0 |Error'
import asyncio
import json
import os

from updown.marketdata.gate.trade_client import GateApiError, GateTradeClient

SYMBOL = os.environ["PROBE_SYMBOL"]


async def main() -> None:
    c = GateTradeClient(
        os.environ.get("GATE_TESTNET_API_KEY", "").strip(),
        os.environ.get("GATE_TESTNET_API_SECRET", "").strip(),
    )
    assert c.is_testnet
    try:
        size = int(str((await c.get_position(SYMBOL)).get("size", 0) or 0))
    except GateApiError as exc:
        if "POSITION_NOT_FOUND" not in str(exc):
            raise
        size = 0
    orders = [(x.get("text"), x.get("size")) for x in await c.list_orders(SYMBOL)]
    stops = [
        (x.get("initial", {}).get("text"), x.get("initial", {}).get("size"))
        for x in await c.list_stops(SYMBOL)
    ]
    print("P0 " + json.dumps({"symbol": SYMBOL, "position": size, "orders": orders, "stops": stops},
                             ensure_ascii=False))


asyncio.run(main())
PY
