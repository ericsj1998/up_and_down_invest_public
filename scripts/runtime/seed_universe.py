"""스크리닝 유니버스 적재 — 후보 목록에서 **브로커가 말하는** 시장·시총으로 상위 N 을 고르고,
종목 행 · 일봉 · 재무 사실까지 채운다 (T260 · 사용자 요구 2026-09-10 "S&P 500 상위 100 은
바로 적재").

    uv run python scripts/runtime/seed_universe.py --top 100 --write-universe --candles --facts

단계:
  1. 후보(`config/fundamentals/sp500_candidates.txt`) → 토스 `/api/v1/stocks`(상장 시장 · 상태 ·
     발행주식수) + `/api/v1/prices`(현재가) → 시총 = 현재가 x 발행주식수 → 보통주(`STOCK`) ·
     `ACTIVE` · NASDAQ/NYSE 만 남기고 상위 N.
  2. `--write-universe`: `config/fundamentals/universe.yml` 을 시장별 목록으로 다시 쓴다.
  3. `instruments` 행 upsert(이름은 토스 영문명).
  4. `--candles`: 판과 같은 캐시(`StoredCandles`)로 일봉을 `--since` 부터 받아 저장
     (처음엔 전 구간 · 다음엔 꼬리).
  5. `--facts`: 사실이 없는 종목만 EDGAR companyfacts 를 받아 저장.

⚠️ 시총 순위는 **토스가 준 발행주식수 x 현재가**다 — 지수 편입 비중이 아니다. 후보 목록은
위키백과 S&P 500 구성 종목(수집일은 파일 머리)이고, 시장·종목명·시총은 전부 브로커가 답한다
(코드에 종목을 박지 않는다).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.fundamentals import load_fundamentals_config
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.session import load_calendar
from updown.common.logging.setup import configure_logging, get_logger
from updown.marketdata.fundamentals.edgar import EdgarAdapter
from updown.marketdata.fundamentals.repository import FundamentalsRepository
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.provider import MarketDataProvider, fundamentals_adapter
from updown.marketdata.toss.adapter import TossAdapter
from updown.orchestration.walkforward.stored_candles import StoredCandles

_logger = get_logger("scripts.seed_universe")

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "config" / "fundamentals" / "sp500_candidates.txt"
UNIVERSE = ROOT / "config" / "fundamentals" / "universe.yml"
MARKETS = {"NASDAQ": Market.NASDAQ, "NYSE": Market.NYSE}
"""토스 `market` → 우리 시장. AMEX · US_ETC 는 능력표·비용표가 없어 뺀다."""


def read_candidates(path: Path) -> list[str]:
    """후보 목록 — `#` 줄과 빈 줄은 건너뛴다."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            out.append(text.upper())
    return out


def rank_by_cap(
    infos: list[dict[str, object]], prices: dict[str, Decimal]
) -> list[tuple[str, Market, str, Decimal]]:
    """보통주 · 상장 중 · NASDAQ/NYSE 만 남겨 시총 내림차순 — `(심볼, 시장, 영문명, 시총)`."""
    rows: list[tuple[str, Market, str, Decimal]] = []
    for info in infos:
        symbol = str(info.get("symbol") or "")
        market = MARKETS.get(str(info.get("market") or ""))
        shares = info.get("sharesOutstanding")
        price = prices.get(symbol)
        if (
            not symbol
            or market is None
            or info.get("status") != "ACTIVE"
            or info.get("securityType") != "STOCK"
            or not isinstance(shares, str)
            or price is None
        ):
            continue
        try:
            cap = Decimal(shares) * price
        except ArithmeticError:
            continue
        name = str(info.get("englishName") or info.get("name") or symbol)
        rows.append((symbol, market, name, cap))
    rows.sort(key=lambda row: row[3], reverse=True)
    return rows


def write_universe(path: Path, picked: list[tuple[str, Market, str, Decimal]], top: int) -> None:
    """`universe.yml` — 시장별 목록. 사람이 읽을 주석에 출처·날짜·기준을 남긴다."""
    by_market: dict[str, list[str]] = {}
    for symbol, market, _name, _cap in picked:
        by_market.setdefault(market.value, []).append(symbol)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        "# 스크리닝 유니버스 (T255 · T260) — `scripts/runtime/seed_universe.py` 가 만든다.",
        "# 손으로 고쳐도 되지만 다음 실행이 덮어쓴다.",
        f"# 기준: S&P 500 후보 중 토스 시총(발행주식수 x 현재가) 상위 {top} · {stamp}.",
        "# 시장은 토스 `/api/v1/stocks` 의 상장 시장이다 — 코드에 종목을 박지 않는다.",
        "",
    ]
    for market in sorted(by_market):
        lines.append(f"{market}:")
        lines.extend(f"  - {symbol}" for symbol in sorted(by_market[market]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자."""
    parser = argparse.ArgumentParser(description="스크리닝 유니버스 적재 (T260)")
    parser.add_argument("--candidates", type=Path, default=CANDIDATES, help="후보 목록 파일")
    parser.add_argument("--top", type=int, default=100, help="시총 상위 N")
    parser.add_argument("--write-universe", action="store_true", help="universe.yml 을 다시 쓴다")
    parser.add_argument("--candles", action="store_true", help="일봉을 캐시에 채운다")
    parser.add_argument("--since", default="2020-01-01", help="일봉 시작일 (UTC)")
    parser.add_argument("--facts", action="store_true", help="EDGAR 사실을 받는다 (없는 종목만)")
    parser.add_argument("--dry", action="store_true", help="고르기만 하고 아무것도 쓰지 않는다")
    return parser


async def _warm_candles(
    toss: TossAdapter, repo: CandleRepository, instruments: list[Instrument], since: datetime
) -> None:
    """일봉을 판과 같은 캐시로 채운다 — 진행 줄마다 브로커 요청 누계를 같이 적는다."""
    end = datetime.now(UTC)
    stored = StoredCandles(toss, repo, calendar=load_calendar())
    t1 = time.perf_counter()
    before = toss.requests
    total = len(instruments)
    for i, instrument in enumerate(instruments, 1):
        tag = f"[{i}/{total}] {instrument.symbol:<6}"
        try:
            rows = await stored.get_candles(instrument, Timeframe.D1, since, end)
            print(f"  {tag} 일봉 {len(rows):>5} (요청 누계 {toss.requests - before})")
        except Exception as exc:
            print(f"  {tag} 실패: {str(exc)[:100]}")
    took = time.perf_counter() - t1
    print(
        f"일봉 완료 {took:.0f}s · 브로커 요청 {toss.requests - before} · "
        f"받음 {stored.fetched} · DB {stored.served}"
    )


async def _load_facts(repo: FundamentalsRepository, symbols: list[str]) -> None:
    """사실이 없는 종목만 EDGAR 에서 받아 저장한다."""
    settings = load_settings()
    adapter = fundamentals_adapter(settings, load_fundamentals_config())
    if not isinstance(adapter, EdgarAdapter):
        raise SystemExit("재무 어댑터가 EDGAR 가 아니다")
    have = {symbol for symbol, _ in await repo.symbols()}
    todo = [s for s in symbols if s not in have]
    print(f"EDGAR: 이미 있음 {len(have & set(symbols))} · 받을 것 {len(todo)}")
    t2 = time.perf_counter()
    ok = 0
    for i, symbol in enumerate(todo, 1):
        try:
            facts = await adapter.facts(symbol)
            count = await repo.upsert_facts(facts)
            ok += 1
            print(f"  [{i}/{len(todo)}] {symbol:<6} 사실 {count:>6}")
        except Exception as exc:
            print(f"  [{i}/{len(todo)}] {symbol:<6} 실패: {str(exc)[:100]}")
    print(f"EDGAR 완료 {time.perf_counter() - t2:.0f}s · 성공 {ok}/{len(todo)}")


async def main() -> None:
    """적재한다."""
    args = build_parser().parse_args()
    settings = load_settings()
    configure_logging(settings.log_level)
    candidates = read_candidates(args.candidates)
    print(f"후보 {len(candidates)}개 ({args.candidates})")

    async with MarketDataProvider() as provider:
        toss = provider.adapter_for(Market.NASDAQ)
        if not isinstance(toss, TossAdapter):
            raise SystemExit("NASDAQ 조회 어댑터가 토스가 아니다 — TOSS_MARKETDATA_* 를 확인한다")
        t0 = time.perf_counter()
        infos = await toss.stock_info(candidates)
        prices = await toss.last_prices([str(i.get("symbol")) for i in infos if i.get("symbol")])
        ranked = rank_by_cap(infos, prices)
        picked = ranked[: args.top]
        print(
            f"토스 정보 {len(infos)} · 현재가 {len(prices)} · 순위 후보 {len(ranked)} · "
            f"상위 {len(picked)} ({time.perf_counter() - t0:.1f}s · 요청 {toss.requests})"
        )
        for i, (symbol, market, name, cap) in enumerate(picked[:10], 1):
            print(f"  {i:>3} {symbol:<6} {market.value:<6} {name[:28]:<28} {cap / 10**9:,.0f}B")
        skipped = [str(i.get("symbol")) for i in infos if str(i.get("market")) not in MARKETS]
        if skipped:
            tail = " …" if len(skipped) > 12 else ""
            print(f"  시장 밖(AMEX·기타)이라 뺀 것: {skipped[:12]}{tail}")
        if args.dry:
            return
        if args.write_universe:
            write_universe(UNIVERSE, picked, args.top)
            print(f"universe.yml 갱신: {UNIVERSE}")

        engine = create_engine(settings.database_url)
        factory = create_session_factory(engine)
        candles_repo = CandleRepository(factory)
        facts_repo = FundamentalsRepository(factory)
        try:
            instruments: list[Instrument] = []
            for symbol, market, name, _cap in picked:
                instrument = Instrument(market, symbol, name, AssetType.STOCK, Currency.USD)
                await candles_repo.upsert_instrument(instrument)
                instruments.append(instrument)
            print(f"instruments upsert {len(instruments)}")
            if args.candles:
                since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
                await _warm_candles(toss, candles_repo, instruments, since)
            if args.facts:
                await _load_facts(facts_repo, [i.symbol for i in instruments])
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
