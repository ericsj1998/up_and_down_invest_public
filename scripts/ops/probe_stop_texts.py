"""Gate 조건부 손절 전부 — 종목 · 크기 · 발동가 · text(몫 키) · 감량 전용 · 만든 시각.

읽기 전용 · api 컨테이너 안에서 돈다.

    bash scripts/ops/remote.sh scripts/ops/probe_stop_texts.py

왜(2026-09-30): 1.26.1 되살리기 뒤 CRV_USDT 에 같은 발동가 손절이 둘 — 어느 키로 걸렸는지(text)를
봐야 `stops_for` 가 옛 것을 왜 못 알아봤는지 가른다(T328).
🔴 키 · 주문 id 는 안 찍는다(id 는 끝 4자리만).
"""

import asyncio
import datetime as dt
import os

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    rows = await c.list_stops("CRV_USDT")
    print("STOPS", len(rows))
    for row in rows:
        ini = row.get("initial", {}) or {}
        trg = row.get("trigger", {}) or {}
        made = row.get("create_time")
        when = (
            dt.datetime.fromtimestamp(float(made), dt.UTC).strftime("%m-%d %H:%M:%S")
            if made
            else "?"
        )
        print(
            "  ",
            ini.get("contract"),
            "size=",
            ini.get("size"),
            "trigger=",
            trg.get("price"),
            "rule=",
            trg.get("rule"),
            "reduce_only=",
            ini.get("reduce_only"),
            "close=",
            ini.get("close"),
            "text=",
            ini.get("text") or row.get("text"),
            "made=",
            when,
            "id_tail=",
            str(row.get("id", ""))[-4:],
            "status=",
            row.get("status"),
        )
    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
