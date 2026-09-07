"""Gate 실계좌 프로브 (읽기 전용) — api 컨테이너 안에서 돈다.

`bash scripts/ops/remote.sh scripts/ops/probe_gate.py`. 계좌(total/available/order_margin) ·
포지션 모드(single 이어야 한다) · 열린 포지션 · 대기 주문(종목별로 물어야 나온다) · 조건부 손절 ·
화이트리스트. 값은 요약만 찍는다 — 키는 절대 안 찍는다.
"""

import asyncio
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SYMS = ["BTC_USDT", "ETH_USDT", "XRP_USDT", "DOGE_USDT", "ADA_USDT", "NEAR_USDT"]


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음 — 이 컨테이너는 실계좌가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    a = await c.get_account()
    print(
        "ACCOUNT total=",
        a.get("total"),
        "available=",
        a.get("available"),
        "position_margin=",
        a.get("position_margin"),
        "order_margin=",
        a.get("order_margin"),
        "unrealised=",
        a.get("unrealised_pnl"),
    )
    print(
        "MODE in_dual_mode=",
        a.get("in_dual_mode"),
        "position_mode=",
        a.get("position_mode"),
        "(single 이어야 한다 · dual 이면 deploy.md §2-2)",
    )
    try:
        ident = await c.get_identity()
        print("WHITELIST", ident.get("ip_whitelist"))
    except Exception as exc:
        print("WHITELIST -> 못 읽음:", str(exc)[:100])
    rows = await c.get_positions()
    live = [r for r in rows if str(r.get("size", "0")) not in ("0", "0.0")]
    print("POSITIONS", len(live))
    for r in live:
        print(
            "  ",
            r.get("contract"),
            "size=",
            r.get("size"),
            "entry=",
            r.get("entry_price"),
            "lev=",
            r.get("leverage"),
            "margin=",
            r.get("margin"),
            "upnl=",
            r.get("unrealised_pnl"),
            "liq=",
            r.get("liq_price"),
        )
    n_open = 0
    for s in SYMS:
        try:
            opens = await c._request(
                "GET", "/futures/usdt/orders", params={"contract": s, "status": "open"}
            )
        except Exception as exc:
            print("  OPEN", s, "->", str(exc)[:80])
            continue
        for o in opens if isinstance(opens, list) else []:
            n_open += 1
            print(
                "  OPEN",
                s,
                "size=",
                o.get("size"),
                "price=",
                o.get("price"),
                "tif=",
                o.get("tif"),
                "text=",
                o.get("text"),
            )
    print("OPEN_ORDERS", n_open, "(있으면 배포하지 않는다 — 입양이 진입 지정가를 거둔다)")
    try:
        stops = await c._request("GET", "/futures/usdt/price_orders", params={"status": "open"})
        stops = stops if isinstance(stops, list) else []
        print("STOP_ORDERS", len(stops))
        for st in stops:
            ini, trg = st.get("initial", {}), st.get("trigger", {})
            print(
                "  STOP",
                ini.get("contract"),
                "size=",
                ini.get("size"),
                "trigger=",
                trg.get("price"),
            )
    except Exception as exc:
        print("STOP_ORDERS -> 못 읽음:", str(exc)[:80])


asyncio.run(main())
