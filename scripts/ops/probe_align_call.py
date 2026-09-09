# ruff: noqa: E501
"""데모 컨테이너 — 실제 원장 기록 + 실제 거래소 클라이언트로 `LiveRunner._align_fee` 를 직접 불러 본다 (DB 쓰기 없음)."""

import asyncio
import os
import traceback
import uuid
from types import SimpleNamespace

import sqlalchemy as sa
import structlog

from updown.common.db.models.walkforward import WalkforwardTrade
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.marketdata.gate.trade_client import GateTradeClient
from updown.orchestration.walkforward import live_runner as lr
from updown.orchestration.walkforward.ledger import Ledger
from updown.orchestration.walkforward.store import _to_record  # pyright: ignore[reportPrivateUsage]

RUN_ID = uuid.UUID("52a1407d-0a38-4bdd-93a3-e834750cfc87")


async def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = create_session_factory(engine)
    async with factory() as s:
        rows = (
            (await s.execute(sa.select(WalkforwardTrade).where(WalkforwardTrade.run_id == RUN_ID)))
            .scalars()
            .all()
        )
    await engine.dispose()
    records = [_to_record(r) for r in rows]
    print(
        "records",
        [
            (
                r.trade_id,
                r.outcome.value,
                r.opened_at is not None,
                r.closed_at is not None,
                r.fee_actual,
            )
            for r in records
        ],
    )
    ledger = Ledger()
    for r in records:
        ledger.add(r)
    c = GateTradeClient(os.environ["GATE_TESTNET_API_KEY"], os.environ["GATE_TESTNET_API_SECRET"])
    inst = Instrument(Market.GATE, "DOGE_USDT", "DOGE", AssetType.COIN, Currency.USD)

    class Orders:
        async def position_closes(self, instrument: Instrument, limit: int = 30):
            return await c.position_closes(instrument.symbol, limit=limit)

    async def spec() -> dict[str, str]:
        return await c.contract("DOGE_USDT")

    async def persist() -> None:
        print("  (persist called — 실제로는 안 쓴다)")

    fake = SimpleNamespace(
        _session=SimpleNamespace(ledger=ledger),
        _orders=Orders(),
        instrument=inst,
        _contract_spec=spec,
        _log=structlog.get_logger("probe"),
        _persist=persist,
    )
    try:
        await lr.LiveRunner._align_fee(fake, "0579b894b9fb")  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    except Exception:
        traceback.print_exc()
    after = next(r for r in ledger.records if r.trade_id == "0579b894b9fb")
    print(
        "after:",
        "cost_pct",
        after.cost_pct,
        "fee_actual",
        after.fee_actual,
        "gain_pct",
        after.gain_pct,
    )


asyncio.run(main())
