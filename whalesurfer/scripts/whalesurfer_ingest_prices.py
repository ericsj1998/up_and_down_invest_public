"""WhaleSurfer 1단계 일봉 적재(T443 D2 · 토스) — 13F 보유 CUSIP 의 티커를 모아 토스 미국 주식 일봉을 `candles` 에 넣는다 (2026-10-09).

- 티커 = `cache/whalesurfer/cusip_figi.json`(OpenFIGI · exch US). 거래소 = SEC `company_tickers_exchange.json`(Nasdaq · NYSE 만 · 그 밖은 건너뜀).
- 토스 조회 어댑터(`MarketDataProvider.adapter_for`) · 1d · 2012-01-01 ~ 어제 · 상장 폐지 종목은 `UnknownSymbolError` → 빠진 목록에 적는다(생존 편향 크기).
- 🔴 로컬은 **실계좌 서버의 토스 프록시**를 거친다 — 요청 사이 `--pause`(기본 1.5초) 로 느리게. 띄우기 전 `--count` 로 수 · 예상 시간을 본다.
- 이미 DB 에 최근(10일 안) 일봉이 있는 종목은 건너뛴다(이어 돌리기). 한 종목의 토스 오류는 실패 목록에 적고 계속.

    set -a; . ./.env.dev; set +a
    uv run --no-sync python whalesurfer/scripts/whalesurfer_ingest_prices.py --count
    uv run --no-sync python whalesurfer/scripts/whalesurfer_ingest_prices.py [--pause 1.5] [--limit N]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.universe import to_instrument
from updown.marketdata.provider import MarketDataProvider, edgar_client
from updown.marketdata.toss.client import TossApiError, UnknownSymbolError

ROOT = Path(__file__).resolve().parents[2]
FIGI_CACHE = ROOT / "cache" / "whalesurfer" / "cusip_figi.json"
EXCH_CACHE = ROOT / "cache" / "whalesurfer" / "company_tickers_exchange.json"
MISSING = ROOT / "cache" / "whalesurfer" / "prices_missing.json"
EXCH_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
EXCH_MAP = {"Nasdaq": Market.NASDAQ, "NYSE": Market.NYSE}
START = datetime(2012, 1, 1, tzinfo=UTC)


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


async def exchange_table() -> dict[str, tuple[Market, str]]:
    """티커 → (시장, 회사 이름) — SEC 거래소 표(파일 캐시)."""
    if not EXCH_CACHE.exists():
        client = edgar_client(load_settings())
        text = await client.get_text(EXCH_URL)
        EXCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        EXCH_CACHE.write_text(text, encoding="utf-8")
    raw = cast("dict[str, Any]", json.loads(EXCH_CACHE.read_text(encoding="utf-8")))
    fields = cast("list[str]", raw["fields"])
    i_t, i_e, i_n = fields.index("ticker"), fields.index("exchange"), fields.index("name")
    out: dict[str, tuple[Market, str]] = {}
    for row in cast("list[list[Any]]", raw["data"]):
        mk = EXCH_MAP.get(str(row[i_e] or ""))
        if mk is not None:
            out[str(row[i_t]).upper()] = (mk, str(row[i_n]))
    return out


async def last_ts(factory: Any, ident: int, tf: Timeframe) -> datetime | None:
    """그 종목 · 시간축의 마지막 봉 시각(없으면 None) — 최근 봉이 이미 있으면 건너뛴다(이어 돌리기).

    2026-10-09: 처음엔 "2013 년 전 첫 봉" 으로 건너뛰었는데 2012 년 뒤 상장한 종목(절반)은 매번
    다시 받았다. 토스 적재는 2012 ~ 어제를 한 번에 받으므로 **마지막 봉이 최근**이면 끝난 종목이다.
    """
    async with factory() as session:
        result = await session.execute(
            sa.text("SELECT max(ts) FROM candles WHERE instrument_id = :i AND timeframe = :t"),
            {"i": ident, "t": tf.value},
        )
    return cast("datetime | None", result.scalar_one_or_none())


def wanted_tickers() -> list[str]:
    figi = cast("dict[str, dict[str, Any]]", json.loads(FIGI_CACHE.read_text(encoding="utf-8")))
    return sorted(
        {
            str(v["ticker"]).upper()
            for v in figi.values()
            if v.get("ticker") and v.get("exch") == "US"
        }
    )


async def main() -> int:
    count_only = "--count" in sys.argv
    pause = float(sys.argv[sys.argv.index("--pause") + 1]) if "--pause" in sys.argv else 1.5
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    # SPY = T443 대조군(같은 기간 지수). 거래소 표에 없으면 NYSE 로.
    tickers = sorted(set(wanted_tickers()) | {"SPY"})
    exch = await exchange_table()
    exch.setdefault("SPY", (Market.NYSE, "SPDR S&P 500 ETF TRUST"))
    plan = [(t, *exch[t]) for t in tickers if t in exch]
    skipped = [t for t in tickers if t not in exch]
    by_mk = {mk.value: sum(1 for _, m, _n in plan if m is mk) for mk in EXCH_MAP.values()}
    log(
        f"OpenFIGI 티커 {len(tickers)} · 거래소 표에 있음 {len(plan)}({by_mk}) · 없음(그 밖 거래소 · 폐지) {len(skipped)}"
    )
    log(f"예상: 종목당 요청 1 · 간격 {pause}s → 약 {len(plan) * pause / 60:.0f}분(프록시 경유)")
    if count_only:
        return 0
    settings = load_settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    repo = CandleRepository(factory)
    adapter = MarketDataProvider().adapter_for(Market.NASDAQ)
    template = to_instrument("AAPL")
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    missing: list[str] = []
    failed: list[str] = []
    done = 0
    t0 = time.time()
    try:
        for i, (sym, mk, name) in enumerate(plan[: limit or len(plan)], 1):
            inst = replace(template, symbol=sym, market=mk, name=name)
            ident = await repo.upsert_instrument(inst)
            last = await last_ts(factory, ident, Timeframe("1d"))
            if last is not None and last >= end - timedelta(days=10):
                continue
            try:
                bars = await adapter.get_candles(inst, Timeframe("1d"), START, end)
            except UnknownSymbolError:
                missing.append(sym)
                await asyncio.sleep(pause)
                continue
            except TossApiError as exc:
                # 2026-10-09 실측: STRL 에서 프록시 424("토스 응답이 JSON 이 아니다" · 응답이 잘림)로
                # 전체가 죽었다(4000/4743 · 12:49 KST). 한 종목의 오류로 멈추지 않는다 — 실패 목록에 적고 간다.
                failed.append(sym)
                log(f"🔴 {sym} 실패 {str(exc)[:80]}")
                await asyncio.sleep(pause)
                continue
            if bars:
                await repo.ensure_partitions_for(b.ts for b in bars)
                await repo.upsert_candles(ident, bars)
            done += 1
            if i % 25 == 0:
                log(
                    f"{i}/{len(plan)} · 넣음 {done} · 없음 {len(missing)} · {time.time() - t0:5.0f}s"
                )
            await asyncio.sleep(pause)
    finally:
        await engine.dispose()
        MISSING.write_text(
            json.dumps(
                {"missing": missing, "failed": failed, "skipped_exchange": skipped},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    log(
        f"끝 · 넣음 {done} · 토스에 없음 {len(missing)} · 실패 {len(failed)} · "
        f"거래소 밖 {len(skipped)}"
    )
    print("INGEST_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
