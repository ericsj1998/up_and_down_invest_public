"""XRP_USDT 를 32계약 줄인다(감량 전용 시장가).

1.27.1 자동 전환 뒤 입양 기록이 14:00Z 에 51계약을 한 번 더 사서 83계약(원장은 51 · 손절 -51)이
된 것을 원장과 맞춘다. ⛔ 사용자 지시 뒤에만.

안전 검사: 포지션이 정확히 83 이고 손절이 -51 하나일 때만 낸다. 다르면 아무것도 안 한다.

    bash scripts/ops/remote.sh scripts/ops/trim_xrp_32_20260930.py
"""

from __future__ import annotations

import asyncio

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider

SYM = "XRP_USDT"
EXPECT_SIZE = 83
EXPECT_STOP = -51
TRIM = -32


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    trade = orders._trade
    pos = next((p for p in await trade.get_positions() if p.get("contract") == SYM), None)
    size = int(pos.get("size", 0)) if pos else 0
    stops = [
        r for r in await trade.list_stops(None) if (r.get("initial") or {}).get("contract") == SYM
    ]
    stop_sizes = [int((r.get("initial") or {}).get("size", 0)) for r in stops]
    print("전:", SYM, "포지션", size, "손절", stop_sizes)
    if size != EXPECT_SIZE or stop_sizes != [EXPECT_STOP]:
        print("상태가 예상과 다르다 — 아무것도 안 한다")
        return 1
    made = await trade.place_order(
        SYM, TRIM, idempotency_key="ops-xrp-trim-20260930", reduce_only=True
    )
    print("주문:", made.get("id"), made.get("size"), made.get("status"), made.get("fill_price"))
    await asyncio.sleep(2)
    pos = next((p for p in await trade.get_positions() if p.get("contract") == SYM), None)
    print("후:", SYM, "포지션", int(pos.get("size", 0)) if pos else 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
