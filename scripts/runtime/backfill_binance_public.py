# ruff: noqa: E501
"""바이낸스 USDT-M 선물 **공개** 과거 봉을 `candles`(시장 BINANCE)에 적재한다 (T279 129차 준비 · 2026-09-19).

왜 따로 있나:
  · 정규 백필(`backfill_cli.py`)은 심볼로 시장을 정하는데 `BTC_USDT` 는 Gate 다 — 같은 심볼을 BINANCE 로 받을 길이 없다.
  · 시세 어댑터(`MarketDataProvider.adapter_for(BINANCE)`)는 **주문이 나가는 곳**(로컬 데모 = 테스트넷)을 본다. 테스트넷 과거 봉은 실제 역사가 아니다.
  · 그래서 연구용 과거 봉만 라이브 공개 끝점(`fapi.binance.com/fapi/v1/klines` · 키 불필요)에서 읽는다. 주문·계정과 무관하고 어댑터를 만들지 않는다(절대 규칙 #0 밖).
Gate 는 시간축마다 최근 10,000봉만 주지만 바이낸스는 과거 제한이 없어 **선물 가격 4년+ 창**을 만들 수 있다(사용자 2026-09-19: "Gate·바이낸스 기준이 가장 중요").

요청 한도: klines limit=1500 은 가중치 10 · 분당 2,400 → 호출마다 0.4초 쉬어 분당 1,500 아래로 잡는다. 429/418 이면 즉시 멈춘다(밴 연장 금지 — 2026-09-03 실측).
이어받기: 시간축마다 DB 의 마지막 봉 다음부터 받는다. 닫히지 않은 마지막 봉은 버린다.

    set -a; . ./.env.dev; set +a
    uv run python scripts/runtime/backfill_binance_public.py --symbols BTC_USDT,ETH_USDT --start 2022-01-01 --timeframes 1h,15m,4h,1d
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Market, Timeframe
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.universe import to_instrument

BASE = "https://fapi.binance.com/fapi/v1/klines"
STEP = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
PAUSE = 0.4


def fetch(symbol: str, interval: str, start_ms: int) -> list[list]:
    url = f"{BASE}?symbol={symbol.replace('_', '')}&interval={interval}&startTime={start_ms}&limit=1500"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as err:
        if err.code in (418, 429):
            raise SystemExit(
                f"바이낸스 요청 한도({err.code}) — 멈춘다. 밴이 풀린 뒤 다시 돌리면 이어받는다."
            ) from err
        raise


async def ensure_instrument(repo: CandleRepository, symbol: str) -> tuple[int, Instrument]:
    """Gate 의 같은 심볼 정의를 시장만 BINANCE 로 바꿔 등록한다(둘 다 USDT 무기한 · 통화 같음)."""
    item = replace(to_instrument(symbol), market=Market.BINANCE)
    ident = await repo.upsert_instrument(item)
    return ident, item


async def run(symbols: list[str], frames: list[str], start: datetime, resume: bool) -> int:
    engine = create_engine(load_settings().database_url)
    repo = CandleRepository(create_session_factory(engine))
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    calls = 0
    try:
        for symbol in symbols:
            ident, item = await ensure_instrument(repo, symbol)
            for frame in frames:
                tf = Timeframe(frame)
                step_ms = STEP[frame] * 60_000
                last = await repo.last_ts(ident, tf) if resume else None
                cur = int(
                    (last + timedelta(minutes=STEP[frame]) if last else start).timestamp() * 1000
                )
                total = 0
                while cur < now_ms - step_ms:
                    rows = fetch(symbol, frame, cur)
                    calls += 1
                    time.sleep(PAUSE)
                    if not rows:
                        break
                    closed = [r for r in rows if int(r[0]) + step_ms <= now_ms]
                    candles = [
                        Candle(
                            instrument=item,
                            timeframe=tf,
                            ts=datetime.fromtimestamp(int(r[0]) / 1000, UTC),
                            open=Decimal(r[1]),
                            high=Decimal(r[2]),
                            low=Decimal(r[3]),
                            close=Decimal(r[4]),
                            volume=Decimal(r[5]),
                        )
                        for r in closed
                    ]
                    if candles:
                        await repo.ensure_partitions_for(c.ts for c in candles)
                        total += await repo.upsert_candles(ident, candles)
                    if len(rows) < 1500:
                        break
                    cur = int(rows[-1][0]) + step_ms
                first = datetime.fromtimestamp(cur / 1000, UTC).date() if total == 0 else None
                print(
                    f"DONE {symbol} {frame} +{total}봉 (호출 누계 {calls})"
                    + (f" · 받을 것 없음(~{first})" if first else ""),
                    flush=True,
                )
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="바이낸스 선물 공개 과거 봉 적재 (연구용)")
    p.add_argument("--symbols", required=True, help="쉼표 구분 · Gate 와 같은 표기(BTC_USDT)")
    p.add_argument("--timeframes", default="1h,15m,4h,1d")
    p.add_argument(
        "--start",
        default="2022-01-01",
        help="처음 받을 때의 시작일(UTC). 이미 있으면 마지막 봉 다음부터",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="DB 의 마지막 봉을 무시하고 --start 부터 다시 받는다(로컬 데모가 넣은 테스트넷 봉을 덮는다)",
    )
    a = p.parse_args()
    frames = [f for f in a.timeframes.split(",") if f]
    unknown = [f for f in frames if f not in STEP]
    if unknown:
        raise SystemExit(f"모르는 시간축: {unknown}")
    start = datetime.fromisoformat(a.start).replace(tzinfo=UTC)
    return asyncio.run(run([s for s in a.symbols.split(",") if s], frames, start, not a.no_resume))


if __name__ == "__main__":
    sys.exit(main())
