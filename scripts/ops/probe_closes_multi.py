"""여러 종목의 거래소 청산 기록 — 매매별 실제 손익(가격 · 수수료 · 펀딩) (실계좌 · 읽기 전용).

    bash scripts/ops/remote.sh scripts/ops/probe_closes_multi.py

왜(2026-09-28): 사용자 "지금 내 매매들 분석해 줘 · SOL 도 BNB 도 실적이 별로" —
원장이 아니라 거래소가 확정한 손익으로 매매를 센다. 🔴 키는 찍지 않는다.
종목은 아래 줄을 고쳐 쓴다(원격에는 환경 변수가 안 넘어간다).
"""

import asyncio
import os
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SYMS = ("SOL_USDT", "XRP_USDT", "BTC_USDT", "DOGE_USDT", "BNB_USDT")
SINCE = datetime(2026, 9, 20, tzinfo=UTC).timestamp()


def when(v: object) -> str:
    try:
        return datetime.fromtimestamp(float(str(v)), UTC).strftime("%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return str(v)


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    print("sym|first_open|close|side|pnl|price_pnl|fee|funding|max_size|long_price|short_price")
    for sym in SYMS:
        closes = await c.position_closes(sym, limit=10)
        for cl in closes if isinstance(closes, list) else []:
            if float(str(cl.get("time", 0))) < SINCE:
                continue
            print(
                "|".join(
                    str(x)
                    for x in (
                        sym,
                        when(cl.get("first_open_time")),
                        when(cl.get("time")),
                        cl.get("side"),
                        cl.get("pnl"),
                        cl.get("pnl_pnl"),
                        cl.get("pnl_fee"),
                        cl.get("pnl_fund"),
                        cl.get("max_size"),
                        cl.get("long_price"),
                        cl.get("short_price"),
                    )
                )
            )
    await c.aclose()


asyncio.run(main())
