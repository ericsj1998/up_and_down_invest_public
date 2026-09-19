#!/usr/bin/env bash
# 크기가 줄었을 때 **거래소 최소 주문**에 걸리나 (T286 H3 · 실계좌 값으로).
# 낙폭 브레이크(x0.5)와 총 명목 상한(줄여서 진입 하한 = 배율/4)이 겹치면 주문이 얼마나 작아지는지,
# 그 크기가 계약 1개를 살 수 있는지 본다. 못 사면 `contracts_for` 가 예외를 내고 원장은 이미 써진 뒤다.
set -uo pipefail
API=$(docker ps --filter "name=api_b" --filter "status=running" --format "{{.Names}}" | head -1)
[ -n "$API" ] || API=$(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}" | head -1)
echo "api: $API"
docker exec -i "$API" python - <<'PY'
import asyncio
import os
from decimal import Decimal

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SYMS = ["BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT"]
SLOT = Decimal("62.1667")   # 자리 예산 (총자본 373 ÷ 6)
LEVER = Decimal(4)


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    print(f"자리 예산 {SLOT} · 선언 배율 {LEVER}x")
    print()
    print(f"{'종목':<11}{'계약당':>10}{'최소':>5} | "
          f"{'전액(4x·1.5)':>13}{'기본(4x)':>10}{'브레이크(2x)':>13}{'하한(1x)':>10}")
    print("-" * 82)
    worst = []
    for sym in SYMS:
        spec = await c.contract(sym)
        mult = Decimal(str(spec["quanto_multiplier"]))
        size_min = int(spec.get("order_size_min", 1))
        px = Decimal(str(spec["last_price"]))
        per = mult * px  # 계약 1개의 명목
        row = [f"{sym:<11}{per:>10.2f}{size_min:>5} | "]
        for label, eff in (("full", LEVER * Decimal("1.5")), ("base", LEVER),
                           ("brake", LEVER / 2), ("floor", LEVER / 4)):
            notional = SLOT * eff
            n = int(notional / per)
            # 🔴 **0계약은 주문이 아니다.** Gate 가 `order_size_min` 을 0 으로 주는 종목이 있어
            #    `n >= size_min` 으로 보면 0계약이 통과한다(이 프로브가 처음에 그렇게 틀렸다).
            need = max(size_min, 1)
            flag = "" if n >= need else " 🔴"
            row.append(f"{n:>6}계약{flag:<6}" if label != "full" else f"{n:>8}계약{flag:<5}")
            if n < need:
                worst.append(f"{sym} {label}")
        print("".join(row))
    print()
    if worst:
        print("🔴 계약 0 이 되는 칸:", ", ".join(worst))
        print("   → contracts_for 가 OrderMappingError 를 내고, 원장은 그 전에 이미 써진다.")
    else:
        print("✅ 어느 칸에서도 계약 1개 이상 — 지금 자본에서는 최소 주문에 안 걸린다.")


asyncio.run(main())
PY
