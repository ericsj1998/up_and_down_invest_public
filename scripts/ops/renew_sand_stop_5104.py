"""SAND_USDT 고아 5,104계약의 운영 손절을 **새로 걸고 옛것을 거둔다** — 24시간 만료 연장.

사용자(2026-10-01): "SAND 는 그냥 둔다 · 손절 걸려 있으면 괜찮다". Gate 조건부는 24시간에 만료되므로
만료 전에 갈아 끼운다. 순서는 **등록 먼저 · 취소 나중**(무방비 창 없음).

안전 검사: 포지션이 +5104 이고 그 종목 조건부가 **정확히 하나 · 러너 몫 손절이 아닐** 때만.
러너가 이어받아 우리 키 손절로 바뀌어 있으면(1.28.1 배포 뒤) 아무것도 안 한다.

    bash scripts/ops/remote.sh scripts/ops/renew_sand_stop_5104.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider

SYM = "SAND_USDT"
EXPECT_SIZE = 5104
TRIGGER = Decimal("0.04129")
PREFIX = "ops-sand-orphan-sl"


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    trade = orders._trade
    pos = next((p for p in await trade.get_positions() if p.get("contract") == SYM), None)
    size = int(pos.get("size", 0)) if pos else 0
    stops = [
        r for r in await trade.list_stops(None) if (r.get("initial") or {}).get("contract") == SYM
    ]
    # Gate 는 text 앞에 "t-" 를 붙여 돌려준다 — 떼고 비교한다.
    names = [str((r.get("initial") or {}).get("text", "")).removeprefix("t-") for r in stops]
    print("전:", SYM, "포지션", size, "조건부", names)
    # 러너의 몫 손절은 `t-<판 6>-<매매 8>-sl-0` 꼴 — 그 꼴이면 러너가 이어받은 것이라 손대지 않는다.
    # (Gate 는 긴 text 를 제 해시로 바꿔 돌려주므로 운영 이름이 그대로 남는다고 믿지 않는다.)
    if size != EXPECT_SIZE or len(stops) != 1 or "-sl-" in names[0]:
        print("상태가 예상과 다르다(포지션 · 조건부 수 · 러너 손절) — 아무것도 안 한다")
        return 1
    old_id = str(stops[0].get("id"))
    stamp = datetime.now(UTC).strftime("%m%d%H%M")
    made = await trade.place_stop(
        SYM, TRIGGER, long=True, size=EXPECT_SIZE, text=f"ops-sand-{stamp}"
    )
    print("새 손절 id:", made)
    await asyncio.sleep(2)
    await trade.cancel_stop(old_id)
    print("옛 손절 거둠:", old_id)
    await asyncio.sleep(2)
    stops = [
        r for r in await trade.list_stops(None) if (r.get("initial") or {}).get("contract") == SYM
    ]
    for r in stops:
        ini = r.get("initial") or {}
        print(" ", ini.get("size"), (r.get("trigger") or {}).get("price"), ini.get("text"))
    return 0 if len(stops) == 1 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
