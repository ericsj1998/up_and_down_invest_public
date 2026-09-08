"""리포트 점검 — 최근 24h Gate 자금 원장 행(type · change · text · 시각) 과 원장 마감 매매."""

import asyncio
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    rows = await c.account_book(limit=300)
    since = datetime.now(UTC) - timedelta(hours=24)
    by: dict[str, Decimal] = defaultdict(Decimal)
    n = 0
    for r in sorted(rows, key=lambda x: float(x.get("time", 0))):
        ts = datetime.fromtimestamp(float(r.get("time", 0)), tz=UTC)
        if ts < since:
            continue
        n += 1
        by[str(r.get("type"))] += Decimal(str(r.get("change", "0")))
        print(
            "  ",
            ts.strftime("%m-%d %H:%M"),
            r.get("type"),
            r.get("change"),
            str(r.get("text", ""))[:40],
        )
    print(
        "ROWS 24h",
        n,
        "sum by type",
        {k: str(v) for k, v in by.items()},
        "total",
        str(sum(by.values())),
    )


asyncio.run(main())
