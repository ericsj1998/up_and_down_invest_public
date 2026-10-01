"""실계좌 Gate 포지션 전부 청산 + 조건부 거두기 (2026-10-01 · 사용자 지시).

순서: 포지션마다 close_position(close=true · size 0 · 시장가 IOC) → 3초 뒤 포지션 0 확인 →
그 종목의 조건부 손절 전부 취소(포지션이 없으니 reduce-only 손절은 뜻이 없다) → 결과 출력.
안전: 포지션이 0 인 종목은 건드리지 않는다 · 주문(진입 지정가)은 없음을 먼저 확인한다(있으면 멈춤).

    bash scripts/ops/remote.sh scripts/ops/close_all_20261001.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    trade = orders._trade
    pos = [p for p in await trade.get_positions() if int(p.get("size", 0) or 0) != 0]
    print("전: 포지션", [(p["contract"], int(p["size"])) for p in pos])
    open_orders = await trade.list_orders(None, "open")
    if open_orders:
        print(
            "미결 주문이 있다 — 멈춘다:", [(o.get("contract"), o.get("size")) for o in open_orders]
        )
        return 1
    stamp = datetime.now(UTC).strftime("%m%d%H%M")
    for p in pos:
        c = p["contract"]
        made = await trade.close_position(
            c, idempotency_key=f"ops-closeall-{stamp}-{c.split('_')[0].lower()}"
        )
        print(
            "청산 주문:",
            c,
            made.get("id"),
            made.get("status"),
            made.get("size"),
            made.get("fill_price"),
        )
    await asyncio.sleep(3)
    left = [p for p in await trade.get_positions() if int(p.get("size", 0) or 0) != 0]
    print("후: 포지션", [(p["contract"], int(p["size"])) for p in left])
    stops = await trade.list_stops(None)
    done = {p["contract"] for p in pos} - {p["contract"] for p in left}
    for r in stops:
        ini = r.get("initial") or {}
        if ini.get("contract") in done:
            await trade.cancel_stop(str(r["id"]))
            print("손절 거둠:", ini.get("contract"), r["id"], (r.get("trigger") or {}).get("price"))
    await asyncio.sleep(2)
    stops = await trade.list_stops(None)
    print(
        "남은 조건부:",
        [
            ((r.get("initial") or {}).get("contract"), (r.get("initial") or {}).get("size"))
            for r in stops
        ],
    )
    return 0 if not left else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
