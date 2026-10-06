"""수수료 맞춤 교차 점검 (읽기 전용 · 값만 · 2026-10-06) — api 컨테이너 안에서 돈다.

`bash scripts/ops/remote.sh scripts/ops/probe_fee_align_1006.py`.
몫 모드 `_align_fee_shares` 가 매분 "생애가 아직 안 끝났다(청산 행 없음)" 로 건너뛰는
매매 다섯의 거래소 청산 행(생애 처음 · 끝 · 수수료)을 원장 진입 · 청산 시각과 나란히 찍는다.
키는 찍지 않는다.
"""

import asyncio
import os
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

# 원장(DB 조회 2026-10-06 10:13Z) — 종목, 진입 UTC, 청산 UTC
LEDGER = [
    ("SAND_USDT", "2026-10-03T03:50", "2026-10-03T10:50"),
    ("SAND_USDT", "2026-10-03T11:55", "2026-10-06T05:29"),
    ("PEOPLE_USDT", "", "2026-10-02T18:39"),
    ("CRV_USDT", "2026-10-05T07:50", "2026-10-05T15:55"),
    ("ADA_USDT", "2026-10-04T22:50", "2026-10-05T15:55"),
]


def utc(sec: object) -> str:
    try:
        return datetime.fromtimestamp(float(str(sec)), UTC).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "-"


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음 — 이 컨테이너는 실계좌가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    for symbol in sorted({row[0] for row in LEDGER}):
        print("==", symbol, "원장", [(a, b) for s, a, b in LEDGER if s == symbol])
        rows = await c.position_closes(symbol, limit=30)
        for r in rows[:6]:
            print(
                "   생애 처음",
                utc(r.get("first_open_time")),
                "| 끝",
                utc(r.get("time")),
                "| side",
                r.get("side"),
                "| 최대 계약",
                r.get("max_size"),
                "| 수수료",
                r.get("pnl_fee"),
                "| 손익",
                r.get("pnl"),
                "| 열쇠",
                sorted(r)[:20] if r is rows[0] else "",
            )


asyncio.run(main())
