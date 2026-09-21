"""지금 실계좌 상태 — 계좌 총액 · 열린 포지션 · 미실현 (읽기 전용).

`bash scripts/ops/remote.sh scripts/ops/probe_live_now3.py`. 키는 절대 안 찍는다.
"""

import asyncio
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


def num(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("이 컨테이너는 실계좌가 아니다")
        return
    client = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    acc = await client.get_account()
    total, unreal = num(acc.get("total")), num(acc.get("unrealised_pnl"))
    print(
        f"계좌 총액 {total:.2f} USDT · 가용 {num(acc.get('available')):.2f} · "
        f"포지션 증거금 {num(acc.get('position_margin')):.2f} · "
        f"주문 증거금 {num(acc.get('order_margin')):.2f}"
    )
    share = 100 * unreal / total if total else 0.0
    print(f"미실현 {unreal:+.3f} USDT ({share:+.2f}% · 총액 대비)")
    print("포지션 (수량 ≠ 0 인 것만)")
    try:
        rows = await client.get_positions()
    except Exception as exc:
        print(f"  포지션 못 읽음: {str(exc)[:120]}")
        return
    held = 0
    for row in rows if isinstance(rows, list) else [rows]:
        size = num(row.get("size"))
        if not size:
            continue
        held += 1
        sym = str(row.get("contract") or row.get("symbol") or "?")
        entry, mark = num(row.get("entry_price")), num(row.get("mark_price"))
        pnl, lev = num(row.get("unrealised_pnl")), row.get("leverage")
        margin = num(row.get("margin"))
        move = (mark / entry - 1) * 100 * (1 if size > 0 else -1) if entry else 0.0
        roe = 100 * pnl / margin if margin else 0.0
        side = "롱" if size > 0 else "숏"
        print(
            f"  {sym:12s} {side} {abs(size):>7.0f}계약 · 진입 {entry:g} · "
            f"현재 {mark:g} ({move:+.2f}%) · 미실현 {pnl:+.3f} USDT · "
            f"증거금 {margin:.2f} ({roe:+.1f}%) · 배율 {lev}"
        )
    print(f"열린 포지션 {held}개")


if __name__ == "__main__":
    asyncio.run(main())
