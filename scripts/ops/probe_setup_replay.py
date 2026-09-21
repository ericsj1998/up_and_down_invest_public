"""**프로덕션 탐지기**를 과거 봉에 그대로 돌려 본다 — "그 봉이 진입 자리였나" 를 손으로 재지 않는다.

`private_strategy` 을 `config/rules/private_strategy.yml` 의 값으로 부른다. 손으로 옮긴 관문과
달리 **라이브가 쓰는 그 함수**라, 결과가 다르면 내 재구성이 틀린 것이다.

봉은 거래소 공개 API 에서 읽는다(키 불필요). 창은 봉마다 "그 봉까지" 로 자른다 — 미래 참조 없음.

    SYMS=ETH_USDT SINCE_H=24 bash scripts/ops/remote.sh scripts/ops/probe_setup_replay.py
"""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import yaml

from updown.analysis.detectors.private_strategy import private_strategy
from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.bands import bollinger
from updown.analysis.indicators.volume import volume_ratio
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

BASE = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
CORE = ("BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT")


async def bars(client: httpx.AsyncClient, symbol: str, limit: int) -> list[Candle]:
    """공개 1H 마감 봉 — 진행 중 봉은 버린다."""
    got = await client.get(BASE, params={"contract": symbol, "interval": "1h", "limit": limit})
    got.raise_for_status()
    where = Instrument(Market.GATE, symbol, symbol, AssetType.COIN, Currency.USD)
    rows = [
        Candle(
            instrument=where,
            timeframe=Timeframe.H1,
            ts=datetime.fromtimestamp(int(r["t"]), tz=UTC),
            open=Decimal(str(r["o"])),
            high=Decimal(str(r["h"])),
            low=Decimal(str(r["l"])),
            close=Decimal(str(r["c"])),
            volume=Decimal(str(abs(float(r["v"])))),
        )
        for r in got.json()
    ]
    return rows[:-1]


async def main() -> None:
    hours = int(os.environ.get("SINCE_H", "16"))
    syms = tuple(x for x in os.environ.get("SYMS", "").split(",") if x) or CORE
    text = Path("config/rules/private_strategy.yml").read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    p = raw["params"]
    cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE)
    print(
        f"룰 {raw.get('version')} · 관통 {p['pen_min_atr']} ATR · 거래량 {p['vol_multiple']}배 · "
        f"손절폭 ≥ {p['entry_stop_floor_pct']}% · 왕복비용 {cost.round_trip_pct}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        for symbol in syms:
            rows = await bars(client, symbol, 700)
            closes = [c.close for c in rows]
            bands = bollinger(closes, period=20, multiple=Decimal(str(p["bb_k"])))
            spans = atr([c.high for c in rows], [c.low for c in rows], closes, 14)
            ratios = volume_ratio([c.volume for c in rows], int(p["vol_period"]))
            print(f"\n=== {symbol} · 마감 봉 {len(rows)}개 · 마지막 {rows[-1].ts:%m-%d %H:%M}")
            found = 0
            for i in range(len(rows) - hours, len(rows)):
                setup = private_strategy(
                    rows[: i + 1],
                    Timeframe.H1,
                    cost.round_trip_pct,
                    bb_period=int(p["bb_period"]),
                    bb_k=Decimal(str(p["bb_k"])),
                    vol_period=int(p["vol_period"]),
                    vol_multiple=Decimal(str(p["vol_multiple"])),
                    sl_atr=Decimal(str(p["sl_atr"])),
                    dir_period=int(p["dir_period"]),
                    dir_bars=int(p["dir_bars"]),
                    pen_min_atr=Decimal(str(p["pen_min_atr"])),
                    entry_stop_floor_pct=Decimal(str(p["entry_stop_floor_pct"])),
                    tilt_newhigh_bars=int(p.get("tilt_newhigh_bars", 0)),
                    tilt_bw_lookback=int(p.get("tilt_bw_lookback", 100)),
                    tilt_bw_rank=Decimal(str(p.get("tilt_bw_rank", "0.5"))),
                )
                bar, upper, prior = rows[i], bands.upper[i], spans[i - 1]
                if setup is None and (upper is None or bar.close <= upper):
                    continue  # 밴드 밖도 아니고 셋업도 아니면 볼 것이 없다
                pen = (bar.close - upper) / prior if upper and prior else Decimal(0)
                raw_ratio = ratios[i]
                ratio = Decimal(str(raw_ratio)) if raw_ratio is not None else Decimal(0)
                if setup is None:
                    print(
                        f"  {bar.ts:%m-%d %H:%M} 밴드 밖이지만 **셋업 없음** · "
                        f"관통 {float(pen):.3f} ATR(직전 ATR {float(prior or 0):g}) · "
                        f"거래량 {float(ratio):.2f}배"
                    )
                    continue
                found += 1
                risk = (setup.avg_entry - setup.stop_loss) / setup.avg_entry * 100
                print(
                    f"  {bar.ts:%m-%d %H:%M} ✅ **셋업** 진입 {setup.avg_entry:g} · "
                    f"손절 {float(setup.stop_loss):.6g} ({float(risk):.2f}%) · "
                    f"크기 승수 {setup.size_mult} · "
                    f"관통 {float(pen):.3f} ATR · 거래량 {float(ratio):.2f}배"
                )
            print(f"  → 이 구간 셋업 {found}건")


if __name__ == "__main__":
    asyncio.run(main())
