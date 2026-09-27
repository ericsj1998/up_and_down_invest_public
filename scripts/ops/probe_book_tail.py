"""자금 원장 최근 줄 — 같은 초 줄의 순서 · 잔고 확인 (읽기 전용 · 금액만).

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
    rows = await c.account_book(limit=8)
    for r in rows:
        t = datetime.fromtimestamp(float(r["time"]), UTC).strftime("%m-%d %H:%M:%S")
        print(t, r.get("id"), r.get("type"), r.get("change"), r.get("balance"))


asyncio.run(main())
