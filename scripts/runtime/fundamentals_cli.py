"""재무 사실 수집·조회 CLI (T243).

    uv run python scripts/runtime/fundamentals_cli.py --symbols AAPL,NVDA,MSFT
        EDGAR → financial_facts
    uv run python scripts/runtime/fundamentals_cli.py --market NASDAQ
        instruments 의 그 시장 전부
    uv run python scripts/runtime/fundamentals_cli.py --show AAPL [--as-of 2025-06-30]
        표 출력 (시점 정합)

`EDGAR_USER_AGENT`(이름 이메일)가 필요하다 — SEC 가 요구한다. 한도(IP 당 10 req/s)는
클라이언트가 지킨다.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from updown.analysis.fundamentals.snapshot import build_snapshot, price_lookup
from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.fundamentals import load_fundamentals_config
from updown.common.domain.instrument import Market
from updown.common.logging.setup import configure_logging
from updown.marketdata.fundamentals.repository import FundamentalsRepository
from updown.marketdata.provider import fundamentals_adapter


def build_parser() -> argparse.ArgumentParser:
    """인자 파서."""
    parser = argparse.ArgumentParser(description="재무 사실(EDGAR) 수집·조회")
    parser.add_argument("--symbols", help="쉼표로 여럿 (AAPL,NVDA)")
    parser.add_argument(
        "--market", default="NASDAQ", help="시장 — --symbols 없으면 이 시장의 instruments 전부"
    )
    parser.add_argument("--show", help="표를 출력할 티커")
    parser.add_argument("--as-of", help="표의 기준일 (YYYY-MM-DD · UTC 자정). 없으면 지금")
    return parser


async def main() -> None:
    """수집 또는 조회."""
    args = build_parser().parse_args()
    settings = load_settings()
    configure_logging(settings.log_level)
    config = load_fundamentals_config()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    repo = FundamentalsRepository(factory)
    market = Market(args.market)
    try:
        if args.show:
            when = (
                datetime.fromisoformat(args.as_of).replace(tzinfo=UTC)
                if args.as_of
                else datetime.now(UTC)
            )
            facts = await repo.facts_for(args.show.upper(), filed_until=when)
            years = config.score.percentile_years + 1
            closes = await repo.daily_closes(
                market, args.show.upper(), when - timedelta(days=365 * years), when
            )
            made = build_snapshot(
                facts,
                symbol=args.show.upper(),
                as_of=when,
                price_at=price_lookup(closes),
                config=config,
            )
            print(
                f"{made.symbol} @ {when.date()} · 종가 {made.price} ({made.price_date})"
                f" · 시총 {made.market_cap}"
            )
            print(
                f"  공시 {len(facts)}건 · 최근 {made.latest_filed_at}"
                f" · 백분위 표본 {made.history_points}"
            )
            for m in made.metrics:
                pct = "" if m.percentile is None else f" · 5y {m.percentile:.0f}%"
                val = "—" if m.value is None else f"{m.value:.2f}{m.spec.unit}"
                print(f"  {m.spec.label:<24} {val:>12}{pct}  {m.note}")
            print(
                f"  깃발 {[f.label for f in made.flags]}"
                f" · 점수 {made.score.score} {made.score.note}"
            )
            return

        if not settings.edgar_user_agent:
            raise SystemExit("EDGAR_USER_AGENT 가 비었다 — `.env.dev` 에 '이름 이메일' 을 넣어라")
        symbols = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else await repo.instruments(market)
        )
        adapter = fundamentals_adapter(settings, config)
        try:
            for symbol in symbols:
                facts = await adapter.facts(symbol)
                count = await repo.upsert_facts(facts)
                latest = max((f.filed_at.date() for f in facts), default=None)
                print(f"{symbol}: 사실 {count}건 · 마지막 공시 {latest}")
        finally:
            await adapter.aclose()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
