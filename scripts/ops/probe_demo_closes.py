# ruff: noqa: E501
"""데모(테스트넷) — DOGE 청산 이력 60줄과 매매 0579b8 창(12:03Z~21:54Z · 2026-09-08)의 겹침 (읽기 전용 · 값은 손익뿐)."""

import asyncio
import os
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import GateTradeClient

LO = datetime(2026, 9, 8, 12, 3, tzinfo=UTC).timestamp() - 60
HI = datetime(2026, 9, 8, 21, 54, 33, tzinfo=UTC).timestamp() + 900


async def main() -> None:
    key = os.environ.get("GATE_TESTNET_API_KEY", "")
    sec = os.environ.get("GATE_TESTNET_API_SECRET", "")
    if not (key and sec):
        print("테스트넷 키 없음")
        return
    c = GateTradeClient(key, sec)
    rows = await c.position_closes("DOGE_USDT", limit=60)
    print("DOGE rows:", len(rows), "window", int(LO), int(HI))
    for r in rows[:40]:
        t = float(r.get("time") or 0)
        mark = "<<" if LO <= t <= HI else "  "
        print(
            mark,
            datetime.fromtimestamp(t, UTC).strftime("%m-%d %H:%M"),
            {k: r.get(k) for k in ("side", "pnl", "pnl_fee", "max_size", "long_price", "text")},
        )


asyncio.run(main())
