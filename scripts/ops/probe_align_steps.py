"""데모 컨테이너에서 `_align_fee` 의 단계를 재현 — 어디서 None 으로 빠지나 (읽기 전용)."""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

from updown.marketdata.gate.trade_client import GateTradeClient
from updown.orchestration.walkforward import live_runner as lr


async def main() -> None:
    print(
        "runner has _align_fee:",
        hasattr(lr.LiveRunner, "_align_fee"),
        "| fee_from_close:",
        hasattr(lr, "fee_from_close"),
    )
    key = os.environ.get("GATE_TESTNET_API_KEY", "")
    sec = os.environ.get("GATE_TESTNET_API_SECRET", "")
    c = GateTradeClient(key, sec)
    opened = datetime(2026, 9, 8, 12, 3, 37, tzinfo=UTC)
    closed = datetime(2026, 9, 8, 21, 54, 33, tzinfo=UTC)
    lo, hi = opened.timestamp() - 60, closed.timestamp() + 900
    rows = await c.position_closes("DOGE_USDT", limit=30)
    spec = await c.contract("DOGE_USDT")
    mult = Decimal(str(spec["quanto_multiplier"]))
    print("rows", len(rows), "multiplier", mult)
    picked = None
    for row in rows:
        try:
            ts = float(str(row.get("time") or "nan"))
        except ValueError:
            continue
        if lo <= ts <= hi:
            picked = row
            break
    print(
        "picked:",
        None
        if picked is None
        else {k: picked.get(k) for k in ("time", "pnl_fee", "max_size", "long_price", "side")},
    )
    if picked is not None and hasattr(lr, "fee_from_close"):
        print("fee_from_close:", lr.fee_from_close(picked, mult))


asyncio.run(main())
