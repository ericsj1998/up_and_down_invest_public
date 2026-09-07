"""한 종목의 거래소 주문 원문 — 끝난 주문 · 미결 · 조건부 (api 컨테이너 안 · 값은 이름·수·상태만).

    SYMBOL=NEAR_USDT bash scripts/ops/remote.sh scripts/ops/probe_order.py

왜: 러너가 지정가 체결을 못 알아본 사고(2026-09-06) — `recent_orders` 가 그 주문을 어떻게 돌려주는지
(`finish_as` · `fill_price` · `id`) 를 러너와 **같은 어댑터 경로**로 본다.
"""

# ruff: noqa: E501 — 운영 출력 한 줄이 길다 (사람이 읽는 표)
from __future__ import annotations

import asyncio
import os

from updown.apps.api.admin import instrument_of
from updown.apps.api.exchange import (
    _orders_adapter,  # pyright: ignore[reportPrivateUsage] — 콘솔과 같은 경로
)
from updown.common.domain.instrument import Market

SYMBOL = os.environ.get("SYMBOL", "NEAR_USDT")


async def main() -> None:
    orders = _orders_adapter("GATE")
    instrument = instrument_of(SYMBOL, Market.GATE)
    recent = await orders.recent_orders(instrument)
    print(f"RECENT {len(recent)}")
    for row in recent[:8]:
        print(
            "  id=…{} status={} finish_as={} size={} left={} price={} fill={} text={} create={} finish={}".format(
                str(row.get("id", ""))[-4:],
                row.get("status"),
                row.get("finish_as"),
                row.get("size"),
                row.get("left"),
                row.get("price"),
                row.get("fill_price"),
                row.get("text"),
                row.get("create_time"),
                row.get("finish_time"),
            )
        )
    opened = await orders.open_orders(instrument)
    print(
        f"OPEN {len(opened)}: {[(str(r.get('id', ''))[-4:], r.get('text'), r.get('left')) for r in opened]}"
    )
    stops = await orders.open_stops(instrument)
    print(
        f"STOPS {len(stops)}: {[(str(r.get('id', ''))[-4:], r.get('trigger_price'), r.get('size')) for r in stops]}"
    )
    held = await orders.position_snapshot(instrument)
    print(
        f"POSITION size={held.get('size')} entry={held.get('entry_price')} liq={held.get('liq_price')} margin={held.get('margin')}"
    )


asyncio.run(main())
