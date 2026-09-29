"""XRP_USDT 의 주인 없는 32계약에 조건부 손절(감량 전용 · 1.5117)을 건다.

사용자(2026-09-30): "계약은 줄이지 말고 · 손절만 잘 걸려있으면 돼".

안전 검사: 포지션 83 · 손절이 -51 하나(원장 몫)일 때만 건다.
이미 -32 가 있으면 안 건다.
text 는 우리 몫 키 형식이 아니라(`ops-…`) 러너가 제 것으로 알아보지 않는다 — 대조(`covered`)는
발동가가 있는 조건부를 전부 세므로 83 계약이 지켜진 것으로 본다.

    bash scripts/ops/remote.sh scripts/ops/place_xrp_stop_32_20260930.py
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider

SYM = "XRP_USDT"
EXPECT_SIZE = 83
OWNED_STOP = -51
EXTRA = 32
TRIGGER = Decimal("1.5117")


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    trade = orders._trade
    pos = next((p for p in await trade.get_positions() if p.get("contract") == SYM), None)
    size = int(pos.get("size", 0)) if pos else 0
    stops = [
        r for r in await trade.list_stops(None) if (r.get("initial") or {}).get("contract") == SYM
    ]
    sizes = sorted(int((r.get("initial") or {}).get("size", 0)) for r in stops)
    print("전:", SYM, "포지션", size, "손절", sizes)
    if size != EXPECT_SIZE or sizes != [OWNED_STOP]:
        print("상태가 예상과 다르다 — 아무것도 안 한다")
        return 1
    made = await trade.place_stop(
        SYM, TRIGGER, long=True, size=EXTRA, text="ops-xrp-extra32-sl-20260930"
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
