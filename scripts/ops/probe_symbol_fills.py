"""한 종목의 거래소 체결 · 청산 손익 분해 — 원장 손익과 맞출 때 (실계좌 · 읽기 전용).

    bash scripts/ops/remote.sh scripts/ops/probe_symbol_fills.py

왜(2026-09-28): SOL 한 건(09-27 07:55 진입 · 15:00 가드 청산) 원장 -6.46 대 거래소 -8.32 USDT —
진입 체결가 · 청산 체결가 · 수수료 · 펀딩으로 차이를 가른다. 🔴 키는 찍지 않는다.
종목은 아래 줄을 고쳐 쓴다(원격에는 환경 변수가 안 넘어간다).
"""

import asyncio
import os
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SYM = "SOL_USDT"


def when(v: object) -> str:
    try:
        return datetime.fromtimestamp(float(str(v)), UTC).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(v)


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    print(f"=== {SYM} 청산 기록 (최근 4)")
    closes = await c.position_closes(SYM, limit=4)
    for cl in closes if isinstance(closes, list) else []:
        print(
            f"  {when(cl.get('time'))} side={cl.get('side')} pnl={cl.get('pnl')} "
            f"price_pnl={cl.get('pnl_pnl')} fee={cl.get('pnl_fee')} funding={cl.get('pnl_fund')} "
            f"max_size={cl.get('max_size')} long_price={cl.get('long_price')} "
            f"short_price={cl.get('short_price')} "
            f"first={when(cl.get('first_open_time'))}"
        )
    print(f"=== {SYM} 체결 (최근 12)")
    trades = await c._request(
        "GET", "/futures/usdt/my_trades", params={"contract": SYM, "limit": "12"}
    )
    for t in trades if isinstance(trades, list) else []:
        print(
            f"  {when(t.get('create_time'))} size={t.get('size')} price={t.get('price')} "
            f"fee={t.get('fee')} role={t.get('role')} close_size={t.get('close_size')}"
        )


asyncio.run(main())
