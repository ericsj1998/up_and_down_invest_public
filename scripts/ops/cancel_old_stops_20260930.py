"""1.27.1 자동 전환(T332 · 2026-09-30 22:39 KST) 뒤 남은 옛 매매 id 의 조건부 손절 3건을 거둔다.

⛔ 사용자 지시 뒤에만 돌린다.

왜: 전환 입양이 매매 id 를 익절 주문 이름에서만 되뽑아(몫 모드엔 익절이 없다) 새 id 를 만들었고,
T328 꼬리 비교(매매 8자)가 옛 손절과 안 맞아 종목마다 손절이 둘이 됐다. 새 손절(러너가 붙잡은 id)은
그대로 두고 옛 것만 거둔다. 거두기 전에 그 종목에 새 손절이 살아 있는지 확인하고, 없으면 거두지
않는다(무방비를 만들지 않는다).

    bash scripts/ops/remote.sh scripts/ops/cancel_old_stops_20260930.py
"""

from __future__ import annotations

import asyncio

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider

OLD = {
    "XRP_USDT": "2104919144058060800",  # t-115947-0cffe41c-sl-0 (13:00Z)
    "LINK_USDT": "2104783273048670208",  # t-245bf4-f19aea01-sl-0 (04:00Z)
    "CRV_USDT": "2104746090556293120",  # t-411bb0-cacdb47a-sl-0 (01:32Z)
}
NEW = {
    "XRP_USDT": "2104929100714999808",  # t-e4113c-7a3a79cf-sl-0
    "LINK_USDT": "2104929175994368000",  # t-3acbd9-c819a929-sl-0
    "CRV_USDT": "2104929664899219456",  # t-411bb0-27e1041d-sl-0
}


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    rows = await orders._trade.list_stops(None)
    alive = {str(r.get("id")): str((r.get("initial") or {}).get("contract", "")) for r in rows}
    for symbol, old_id in OLD.items():
        if NEW[symbol] not in alive:
            print(f"{symbol}: 새 손절 {NEW[symbol]} 이 없다 — 옛 것을 거두지 않는다")
            continue
        if old_id not in alive:
            print(f"{symbol}: 옛 손절 {old_id} 은 이미 없다")
            continue
        await orders._trade.cancel_stop(old_id)
        print(f"{symbol}: 옛 손절 {old_id} 거둠 (새 {NEW[symbol]} 유지)")
    left = await orders._trade.list_stops(None)
    print("남은 조건부", len(left))
    for r in left:
        ini = r.get("initial") or {}
        print(
            " ",
            ini.get("contract"),
            ini.get("size"),
            (r.get("trigger") or {}).get("price"),
            ini.get("text"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
