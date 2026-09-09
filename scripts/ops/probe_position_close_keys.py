"""거래소 청산 이력 한 줄의 **열쇠**와 값 — pnl_fee 가 있나 (T236 · 읽기 전용 · 시크릿 없음)."""

import asyncio
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    live = os.environ.get("LIVE_ORDERS", "") == "1" or os.environ.get("GATE_TESTNET", "1") == "0"
    if not (key and sec):
        print("키 없음 — 이 컨테이너는 거래 클라이언트가 없다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL) if live else GateTradeClient(key, sec)
    print("base:", "live" if live else "testnet(default)")
    for contract in ("DOGE_USDT", "BTC_USDT"):
        rows = await c.position_closes(contract, limit=3)
        print(contract, "rows", len(rows))
        for r in rows[:2]:
            print("  keys:", sorted(r.keys()))
            print(
                "  ",
                {
                    k: r.get(k)
                    for k in (
                        "time",
                        "side",
                        "pnl",
                        "pnl_fee",
                        "pnl_fund",
                        "pnl_pnl",
                        "max_size",
                        "accum_size",
                        "long_price",
                        "short_price",
                        "text",
                    )
                },
            )
    trades = await c._request(
        "GET", "/futures/usdt/my_trades", params={"contract": "DOGE_USDT", "limit": "3"}
    )  # pyright: ignore[reportPrivateUsage]
    print(
        "my_trades sample keys:",
        sorted(trades[0].keys()) if isinstance(trades, list) and trades else trades,
    )
    if isinstance(trades, list) and trades:
        print(
            "  ",
            {
                k: trades[0].get(k)
                for k in ("create_time", "size", "price", "fee", "role", "order_id", "text")
            },
        )


asyncio.run(main())
