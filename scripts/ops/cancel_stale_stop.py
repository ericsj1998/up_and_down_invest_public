"""CRV_USDT 의 옛 키 조건부 손절(중복) 하나를 거둔다 — 사용자 지시 2026-09-30 "거둬주고"
(T328 · api 컨테이너 안).

    bash scripts/ops/remote.sh scripts/ops/cancel_stale_stop.py

안전장치: 같은 종목에 조건부가 정확히 둘이고 · 둘의 발동가 · 크기가 같고 · 지우려는 것의 text 가
옛 키(`t-f780d1-…`)이고 id 끝이 9344 일 때만 거둔다. 아니면 아무것도 안 한다. 거둔 뒤 남은 목록을
찍는다.
🔴 키 · 주문 id 전체는 안 찍는다.
"""

import asyncio
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

CONTRACT = "CRV_USDT"
OLD_PREFIX = "t-f780d1-"
ID_TAIL = "9344"


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    try:
        rows = await c.list_stops(CONTRACT)
        print("STOPS 전", len(rows))
        if len(rows) != 2:
            print("🔴 둘이 아니다 — 아무것도 안 함")
            return
        sig = {
            (str((r.get("initial") or {}).get("size")), str((r.get("trigger") or {}).get("price")))
            for r in rows
        }
        if len(sig) != 1:
            print("🔴 발동가 · 크기가 다르다 — 아무것도 안 함", sig)
            return
        old = [
            r
            for r in rows
            if str((r.get("initial") or {}).get("text", "")).startswith(OLD_PREFIX)
            and str(r.get("id", "")).endswith(ID_TAIL)
        ]
        if len(old) != 1:
            print("🔴 옛 키 손절을 하나로 못 골랐다 — 아무것도 안 함", len(old))
            return
        got = await c.cancel_stop(str(old[0]["id"]))
        print("거둠 · 상태", got.get("status"), "· text", (old[0].get("initial") or {}).get("text"))
        left = await c.list_stops(CONTRACT)
        print("STOPS 뒤", len(left))
        for r in left:
            ini = r.get("initial") or {}
            print("  남음", ini.get("size"), (r.get("trigger") or {}).get("price"), ini.get("text"))
    finally:
        await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
