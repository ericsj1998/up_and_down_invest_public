"""instruments 시드 (P0-8-1 · spec §9).

**대상 종목은 `config/backfill.yml` 의 `universe` 가 정한다.** 여기에 목록을 또 두면
설정과 갈라지고, 갈라진 순간 "설정에는 있는데 시드에 없는 종목"을 수집하려 들어
`InstrumentNotFoundError` 가 난다 — 원인이 두 목록의 불일치라 찾기 어렵다.

Phase 0 유니버스는 코인 2종목이다. 넓힐 이유가 없다 — P0-8-7 이 종목당 5m 1년치(10.5만 봉)를
받으므로 종목이 늘면 백필 시간이 선형으로 늘고, Phase 0 의 목표는 "파이프라인이 도는가"이지
"유니버스가 넓은가"가 아니다.

실행:
    uv run python scripts/runtime/seed_instruments.py
    uv run python scripts/runtime/seed_instruments.py --verify   # 업비트에 실재하는지 확인까지

멱등하다. 여러 번 돌려도 `(market, symbol)` 충돌 시 표시 정보만 갱신된다.
"""

import argparse
import asyncio
from pathlib import Path

from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import Instrument, Market
from updown.common.logging.setup import configure_logging, get_logger
from updown.marketdata.ingest.backfill import DEFAULT_SCOPE_PATH, BackfillScope
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.universe import resolve_universe
from updown.marketdata.provider import MarketDataProvider

_logger = get_logger("scripts.seed_instruments")


async def verify_against_upbit(instruments: tuple[Instrument, ...]) -> None:
    """업비트에 실재하는 코드인지 확인한다.

    Args:
        instruments: 검증할 종목.

    Raises:
        SystemExit: 하나라도 실재하지 않는 경우.

    Note:
        오타 하나가 "백필이 도는데 봉이 0개"로 나타나는 것을 막는다. 업비트는 없는 코드에
        404 를 주지만(실측), 시드 단계에서 잡으면 원인이 훨씬 가깝다 (spec §7).
    """
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(Market.UPBIT)
        # `list_markets` 는 `BrokerAdapter` 프로토콜에 없는 업비트 추가 메서드다.
        available = set(await adapter.list_markets())  # pyright: ignore[reportAttributeAccessIssue]

    missing = [inst.symbol for inst in instruments if inst.symbol not in available]
    if missing:
        _logger.error("seed_symbols_not_on_upbit", payload={"missing": missing})
        raise SystemExit(f"업비트에 없는 코드다: {missing}")


async def main() -> None:
    """시드를 적재한다."""
    parser = argparse.ArgumentParser(description="instruments 시드 (P0-8-1)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="업비트 마켓 목록과 대조해 실재하는 코드인지 확인한다 (네트워크 필요)",
    )
    parser.add_argument(
        "--scope", type=Path, default=DEFAULT_SCOPE_PATH, help="유니버스를 읽을 YAML"
    )
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.log_level)

    scope = BackfillScope.load(args.scope)
    instruments = resolve_universe(scope.universe)
    print(f"유니버스 ({args.scope}): {', '.join(scope.universe)}")

    if args.verify:
        await verify_against_upbit(instruments)

    engine = create_engine(settings.database_url)
    repository = CandleRepository(create_session_factory(engine))
    try:
        for instrument in instruments:
            instrument_id = await repository.upsert_instrument(instrument)
            print(f"  {instrument.market.value}:{instrument.symbol} → id={instrument_id}")
    finally:
        await engine.dispose()

    print(f"\n시드 완료: {len(instruments)}종목")


if __name__ == "__main__":
    asyncio.run(main())
