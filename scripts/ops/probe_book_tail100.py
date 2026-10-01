"""자금 원장 최근 100줄 — 시각 · 종류 · 변화 · 잔고 · 계약 (읽기 전용 · 금액만).

`bash scripts/ops/remote.sh scripts/ops/probe_book_tail.py`.
"""

import asyncio
import os
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


async def main() -> None:
    c = GateTradeClient(
        os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"], base_url=LIVE_BASE_URL
    )
    rows = await c.account_book(limit=100)
    for r in rows:
        t = datetime.fromtimestamp(float(r["time"]), UTC).strftime("%m-%d %H:%M:%S")
        print(
            t,
            r.get("id"),
            r.get("type"),
            r.get("change"),
            r.get("balance"),
            r.get("contract") or r.get("text", "")[:30],
        )


asyncio.run(main())
