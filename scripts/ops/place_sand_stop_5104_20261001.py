"""SAND_USDT 의 주인 없는 5,104계약(자동 전환 뒤 원장이 잊은 롱 · 입양 거부)에
조건부 손절(감량 전용)을 건다.

사용자(2026-10-01): 진입 정지 뒤 배너 "이어받기 안 됨 · 조건부 손절 없음 · 입양 거부".
무방비를 먼저 끝낸다.
발동가 = 그 매매의 계획 손절(`wf_trades.planned_stop` 0.04129 · 진입 0.04521 대비 -8.7%).

안전 검사: 포지션이 +5104 이고 그 종목 조건부가 0건일 때만 건다. 아니면 아무것도 안 한다.

    bash scripts/ops/remote.sh scripts/ops/place_sand_stop_5104_20261001.py
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider

SYM = "SAND_USDT"
EXPECT_SIZE = 5104
TRIGGER = Decimal("0.04129")


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    trade = orders._trade
    pos = next((p for p in await trade.get_positions() if p.get("contract") == SYM), None)
    size = int(pos.get("size", 0)) if pos else 0
    stops = [
        r for r in await trade.list_stops(None) if (r.get("initial") or {}).get("contract") == SYM
    ]
    print(
        "전:",
        SYM,
        "포지션",
        size,
        "조건부",
        [int((r.get("initial") or {}).get("size", 0)) for r in stops],
    )
    if size != EXPECT_SIZE or stops:
        print("상태가 예상과 다르다 — 아무것도 안 한다")
        return 1
    made = await trade.place_stop(
        SYM, TRIGGER, long=True, size=EXPECT_SIZE, text="ops-sand-orphan-sl-20261001"
    )
    print("건 손절 id:", made)
    await asyncio.sleep(2)
    stops = [
        r for r in await trade.list_stops(None) if (r.get("initial") or {}).get("contract") == SYM
    ]
    for r in stops:
        ini = r.get("initial") or {}
        print(" ", ini.get("size"), (r.get("trigger") or {}).get("price"), ini.get("text"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
