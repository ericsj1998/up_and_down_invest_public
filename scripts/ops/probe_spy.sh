#!/usr/bin/env bash
# 서버 토스가 SPY(S&P 500 ETF) 일봉을 주는가 — AI 차트 분석 주문의 주식 기본 종목 후보 (값 요약만).
cd ~/updown 2>/dev/null || exit 1
c=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker exec "$c" python -c '
import asyncio
from datetime import UTC, datetime, timedelta
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.provider import MarketDataProvider
async def main():
    p = MarketDataProvider()
    for mk, sym in ((Market.NYSE, "SPY"), (Market.NASDAQ, "SPY"), (Market.NASDAQ, "QQQ")):
        try:
            a = p.adapter_for(mk)
            ins = Instrument(mk, sym, sym, AssetType.STOCK, Currency.USD)
            end = datetime.now(UTC)
            rows = await a.get_candles(ins, Timeframe.D1, end - timedelta(days=20), end)
            print(mk.value, sym, "bars", len(rows), "last", rows[-1].ts.date() if rows else None)
        except Exception as exc:
            print(mk.value, sym, "ERR", str(exc)[:120])
asyncio.run(main())
' 2>&1 | grep -v "^20[0-9][0-9]-"
