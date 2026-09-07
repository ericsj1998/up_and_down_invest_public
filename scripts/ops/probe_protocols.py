"""러너가 어댑터에게 묻는 능력 검사(Protocol isinstance)가 실제 어댑터에 통하는가 — api 컨테이너 안.

    bash scripts/ops/remote.sh scripts/ops/probe_protocols.py

왜: 2026-09-06 고아 사고 — `_pump_fills` 는 `isinstance(orders, OrdersAware)` 가 거짓이면 **조용히**
돌아간다. 그 검사가 실계좌 어댑터에서 거짓이면 지정가 체결을 영원히 못 본다. 출력은 참/거짓뿐이다.
"""

from __future__ import annotations

from updown.apps.api.exchange import _orders_adapter  # pyright: ignore[reportPrivateUsage]
from updown.orchestration.walkforward import live_runner as lr

orders = _orders_adapter("GATE")
print("adapter:", type(orders).__name__)
for name in (
    "OrdersAware",
    "StopAware",
    "PositionAware",
    "LeverageAware",
    "MarginAware",
    "BookAware",
    "PositionLister",
):
    proto = getattr(lr, name, None)
    if proto is None:
        continue
    print(f"  {name:14s} {isinstance(orders, proto)}")
missing = [
    m
    for m in ("open_orders", "open_stops", "recent_orders", "position_snapshot", "cancel_order")
    if not hasattr(orders, m)
]
print("missing methods:", missing or "none")
