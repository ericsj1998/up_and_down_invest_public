"""자금 원장 입출금을 기간 조각으로 거슬러 읽기 (읽기 전용 · 금액만).

`bash scripts/ops/remote.sh scripts/ops/probe_book_kinds.py` — 시각 칸 없이 받으면 약 30 일만 오므로
리포트의 넣은 돈이 비는지 볼 때.
"""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient

DAY = 86400
KST_SHIFT = timedelta(hours=9)


async def main() -> None:
    c = GateTradeClient(
        os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"], base_url=LIVE_BASE_URL
    )
    now = int(time.time())
    total = Decimal(0)
    for k in range(0, 8):
        to = now - k * 25 * DAY
        fr = to - 25 * DAY
        try:
            rows = await c._request(
                "GET",
                f"/futures/{SETTLE}/account_book",
                params={"limit": "100", "type": "dnw", "from": str(fr), "to": str(to)},
            )
        except Exception as exc:  # 조회 실패도 그대로 찍는다
            print("조각", k, "실패", str(exc)[:120])
            continue
        print(
            "조각",
            k,
            (datetime.fromtimestamp(fr, UTC) + KST_SHIFT).strftime("%m-%d"),
            "줄",
            len(rows),
        )
        for r in sorted(rows, key=lambda x: float(x["time"])):
            at = datetime.fromtimestamp(float(r["time"]), UTC) + KST_SHIFT
            print(
                " ",
                at.strftime("%m-%d %H:%M KST"),
                r.get("type"),
                r.get("change"),
                r.get("balance"),
            )
            total += Decimal(str(r.get("change", "0")))
    print("입출금 합", total)


asyncio.run(main())
