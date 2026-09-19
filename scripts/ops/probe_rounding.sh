#!/usr/bin/env bash
# 계약이 정수라 생기는 **내림 손실** — 종목마다 얼마나 다른가 (사용자 지적 2026-09-19).
# 백테스트는 배율을 연속값으로 굴린다(eff = 4 x 기울기). 실제는 계약 수가 정수라 **항상 내림**되고,
# 그만큼 실제 배율이 선언보다 낮다. 즉 4x 로 잰 성적을 3.8x 로 사는 셈이다 — 종목마다 다르게.
set -uo pipefail
API=$(docker ps --filter "name=api_b" --filter "status=running" --format "{{.Names}}" | head -1)
[ -n "$API" ] || API=$(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}" | head -1)
docker exec -i "$API" python - <<'PY'
import asyncio
import os
from decimal import Decimal

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SYMS = ["BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT"]
SLOT = Decimal("62.1667")
LEVER = Decimal(4)
# 기울기 x 브레이크 조합 — 실제로 나오는 유효 배율들
CASES = [("기울기1.5", Decimal("1.5")), ("기울기1.0", Decimal(1)), ("기울기0.5", Decimal("0.5")),
         ("1.5+브레이크", Decimal("0.75")), ("1.0+브레이크", Decimal("0.5")),
         ("0.5+브레이크", Decimal("0.25"))]


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    specs = {}
    for sym in SYMS:
        s = await c.contract(sym)
        specs[sym] = Decimal(str(s["quanto_multiplier"])) * Decimal(str(s["last_price"]))

    print(f"자리 예산 {SLOT} USDT · 선언 배율 {LEVER}x · **실제 유효 배율** (내림 뒤)")
    print()
    head = f"{'종목':<11}{'계약당':>9} | " + "".join(f"{n:>13}" for n, _ in CASES)
    print(head)
    print("-" * len(head))
    loss_by_sym: dict[str, list[Decimal]] = {}
    for sym in SYMS:
        per = specs[sym]
        cells = []
        losses = []
        for _name, mult in CASES:
            want = LEVER * mult                 # 선언 유효 배율
            notional = SLOT * want
            n = int(notional / per)             # ROUND_DOWN
            got = (n * per) / SLOT              # 실제 유효 배율
            loss = (want - got) / want * 100 if want > 0 else Decimal(0)
            losses.append(loss)
            mark = " 🔴" if n < 1 else ""
            cells.append(f"{got:>6.2f}x(-{loss:>4.1f}%){mark}")
        loss_by_sym[sym] = losses
        print(f"{sym:<11}{per:>9.2f} | " + "".join(f"{x:>13}" for x in cells))

    print()
    print("종목별 평균 내림 손실 (여섯 칸 평균):")
    rank = sorted(loss_by_sym.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
    for sym, xs in rank:
        avg = sum(xs) / len(xs)
        bar = "#" * int(avg)
        print(f"  {sym:<11} {avg:>5.1f}%  {bar}")
    allx = [x for xs in loss_by_sym.values() for x in xs]
    print(f"  {'전체 평균':<11} {sum(allx) / len(allx):>5.1f}%")
    print()
    print("읽기: 이것은 '돈을 잃는' 것이 아니라 **더 작게 산다**는 뜻이다(배율 인하와 같다).")
    print("      148차 — 배율을 내리면 수익과 MDD 가 같이 내려가고 수익÷MDD 는 3~5x 구간에서 평평하다.")
    print("      문제는 크기가 아니라 **종목마다 다르다**는 것 — 같은 신호에 SOL 만 작게 산다.")


asyncio.run(main())
PY
