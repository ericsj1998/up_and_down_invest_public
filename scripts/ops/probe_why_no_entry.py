"""돌파 진입이 왜 안 났나 — 룰 0.3 의 관문 넷을 종목별로 찍는다 (읽기 전용 · 키 없음).

**폭(`closed_above_upper`)은 "종가가 밴드 상단 밖" 하나만 본다.** 진입에는 그 위에 관문이 넷 더
있다 — 거래량 ≥ 2배 · 관통 ≥ 0.75 ATR · 4H 방향이 하락 아님 · 초기 손절폭 ≥ 1.4%. 그래서
**폭에 세어진 종목이 진입은 못 할 수 있다.** 밴드 밖으로 마감한 봉마다 그 넷을 나란히 보여 준다.

봉은 **거래소 공개 API**에서 읽는다(키 불필요). DB 의 `candles` 에는 GATE 1H 가 안 쌓이고
라이브 세션은 웹소켓 봉으로 판단하기 때문이다 — DB 로 재구성하면 아예 빈 표가 나온다.

    bash scripts/ops/remote.sh scripts/ops/probe_why_no_entry.py
    SINCE_H=24 SYMS=ADA_USDT,BTC_USDT bash scripts/ops/remote.sh scripts/ops/probe_why_no_entry.py
"""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from updown.analysis.detectors.private_strategy import four_hour_direction
from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.bands import bollinger
from updown.analysis.indicators.volume import volume_ratio
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

BASE = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
CORE = ("BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT")
PEN, VOL, FLOOR, SL_ATR = Decimal("0.75"), Decimal("2.0"), Decimal("1.4"), Decimal("0.2")


def mark(ok: bool) -> str:
    return "✅" if ok else "⛔"


async def bars(client: httpx.AsyncClient, symbol: str) -> list[Candle]:
    """공개 1H 봉 — 마지막(진행 중) 봉은 버린다. 판정은 마감 봉으로만 한다."""
    got = await client.get(BASE, params={"contract": symbol, "interval": "1h", "limit": 700})
    got.raise_for_status()
    instrument = Instrument(Market.GATE, symbol, symbol, AssetType.COIN, Currency.USD)
    out = [
        Candle(
            instrument=instrument,
            timeframe=Timeframe.H1,
            ts=datetime.fromtimestamp(int(row["t"]), tz=UTC),
            open=Decimal(str(row["o"])),
            high=Decimal(str(row["h"])),
            low=Decimal(str(row["l"])),
            close=Decimal(str(row["c"])),
            volume=Decimal(str(abs(float(row["v"])))),
        )
        for row in got.json()
    ]
    now = datetime.now(UTC)
    return [c for c in out if c.ts.replace(minute=0, second=0, microsecond=0) < now][:-1]


async def main() -> None:
    hours = int(os.environ.get("SINCE_H", "16"))
    syms = tuple(x for x in os.environ.get("SYMS", "").split(",") if x) or CORE
    async with httpx.AsyncClient(timeout=30) as client:
        for symbol in syms:
            try:
                rows = await bars(client, symbol)
            except Exception as exc:
                print(f"\n=== {symbol}: 봉 못 읽음 {str(exc)[:80]}", flush=True)
                continue
            last = rows[-1].ts.strftime("%m-%d %H:%M") if rows else "없음"
            print(f"\n=== {symbol} · 마감 봉 {len(rows)}개 · 마지막 {last}", flush=True)
            if len(rows) < 40:
                continue
            closes = [c.close for c in rows]
            bands = bollinger(closes, period=20, multiple=Decimal(2))
            spans = atr([c.high for c in rows], [c.low for c in rows], closes, 14)
            ratios = volume_ratio([c.volume for c in rows], 20)
            hits = 0
            for i in range(max(20, len(rows) - hours), len(rows)):
                bar, upper, prior = rows[i], bands.upper[i], spans[i - 1]
                if upper is None or not prior or bar.close <= upper:
                    continue  # 밴드 밖 마감이 아니면 폭에도 안 세어진다
                hits += 1
                pen = (bar.close - upper) / prior
                raw = ratios[i]
                ratio = Decimal(str(raw)) if raw is not None else Decimal(0)
                way = four_hour_direction(rows[: i + 1], period=20, bars=5)
                risk = (bar.close - (bar.low - SL_ATR * prior)) / bar.close * 100
                ok = ratio >= VOL and pen >= PEN and way >= 0 and risk >= FLOOR
                print(
                    f"  {bar.ts:%m-%d %H:%M} 종가 {bar.close:g} | 밴드 밖 ✅ "
                    f"| 거래량 {mark(ratio >= VOL)} {float(ratio):.2f}배 "
                    f"| 관통 {mark(pen >= PEN)} {float(pen):.2f} ATR "
                    f"| 4H {mark(way >= 0)} {way:+d} "
                    f"| 손절폭 {mark(risk >= FLOOR)} {float(risk):.2f}% "
                    f"→ {'**진입 자리**' if ok else '탈락'}",
                    flush=True,
                )
            if not hits:
                print("  (이 구간에 밴드 밖 마감 없음)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
