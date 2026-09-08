"""00:00Z NEAR pnl 행 · 08:02Z DOGE fee 행의 주문 — 무엇이었나 (실계좌 · 읽기 전용)."""

import asyncio
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    rows = await c.get_positions()
    for r in rows:
        if str(r.get("size", "0")) not in ("0", "0.0"):
            print(
                "POS",
                r.get("contract"),
                "size=",
                r.get("size"),
                "entry=",
                r.get("entry_price"),
                "realised=",
                r.get("realised_pnl"),
                "history_pnl=",
                r.get("history_pnl"),
            )
    for oid in ("123567516108354177", "56013526261917634"):
        try:
            o = await c._request("GET", f"/futures/usdt/orders/{oid}")
            keys = (
                "contract",
                "size",
                "left",
                "price",
                "fill_price",
                "status",
                "finish_as",
                "is_reduce_only",
                "is_close",
                "text",
                "create_time",
                "finish_time",
                "tif",
            )
            print("ORDER", oid, {k: o.get(k) for k in keys})
        except Exception as exc:
            print("ORDER", oid, "->", str(exc)[:160])
    try:
        trades = await c._request("GET", "/futures/usdt/my_trades", params={"limit": "10"})
        for t in trades:
            print(
                "TRADE",
                t.get("contract"),
                "size=",
                t.get("size"),
                "price=",
                t.get("price"),
                "role=",
                t.get("role"),
                "order=",
                t.get("order_id"),
                "time=",
                t.get("create_time"),
            )
    except Exception as exc:
        print("TRADES ->", str(exc)[:160])


asyncio.run(main())
