# ruff: noqa: E501, RUF002
"""SUI_USDT 의 주인 없는 60 계약(11-17 01:00 KST 몫 청산이 58 계약만 나가 남은 롱)을 시장가로 줄인다.

✅ 2026-11-17 09:01:31 KST 실행 — 전 120 · 손절 [-60] → 줄임 주문 175640390390287008 체결 0.7192 → 후 60 · 손절 [-60].

사용자(2026-11-17 아침): "SUI는 네가 닫아줄 수 없나?" — 사건 기록 `docs/incidents/2026-11-17_sui_share_exit_short.md`.
거래소 120 계약 · 원장 몫 합 60 · 걸린 손절 60 → 감량 전용 시장가 −60 으로 거래소를 원장과 맞춘다.

안전 검사(하나라도 아니면 아무것도 안 한다):
  - SUI 포지션이 정확히 +120
  - SUI 조건부 손절이 정확히 하나이고 수량 −60
  - SUI 미결 주문(진입 · 익절 지정가) 0 건
뒤: 3 초 뒤 포지션 +60 · 손절 −60 그대로인지 찍는다.

    bash scripts/ops/remote.sh scripts/ops/reduce_sui_orphan_60_20261117.py
"""

from __future__ import annotations

import asyncio

from updown.common.domain.instrument import Market
from updown.execution.gateway import order_adapter
from updown.marketdata.provider import MarketDataProvider

SYM = "SUI_USDT"
EXPECT_SIZE = 120
EXPECT_STOP = -60
CUT = -60


async def state(trade) -> tuple[int, list[int], int]:
    pos = next((p for p in await trade.get_positions() if p.get("contract") == SYM), None)
    size = int(pos.get("size", 0)) if pos else 0
    stops = [
        int((r.get("initial") or {}).get("size", 0))
        for r in await trade.list_stops(None)
        if (r.get("initial") or {}).get("contract") == SYM
    ]
    opens = [o for o in await trade.list_orders(None, "open") if o.get("contract") == SYM]
    return size, stops, len(opens)


async def main() -> int:
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    orders = order_adapter(quotes, user_id="ops")
    trade = orders._trade
    size, stops, opens = await state(trade)
    print("전:", SYM, "포지션", size, "· 손절", stops, "· 미결 주문", opens)
    if size != EXPECT_SIZE or stops != [EXPECT_STOP] or opens:
        print("상태가 예상과 다르다 — 아무것도 안 한다")
        return 1
    made = await trade.place_order(
        SYM, CUT, idempotency_key="ops-sui-orphan-60-1117", reduce_only=True
    )
    print(
        "줄임 주문:",
        made.get("id"),
        made.get("status"),
        "수량",
        made.get("size"),
        "남음",
        made.get("left"),
        "체결가",
        made.get("fill_price"),
    )
    await asyncio.sleep(3)
    size, stops, opens = await state(trade)
    print("후:", SYM, "포지션", size, "· 손절", stops, "· 미결 주문", opens)
    return 0 if size == EXPECT_SIZE + CUT and stops == [EXPECT_STOP] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
