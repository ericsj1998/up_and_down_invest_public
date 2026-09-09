"""T243 — `financial_facts` 왕복 · `filed_until` 시점 필터 (실제 DB)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from _fundamentals_fixtures import fact
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import Market
from updown.marketdata.fundamentals.repository import FundamentalsRepository

pytestmark = pytest.mark.db


@pytest.fixture
def db_url(migrated_test_database: str) -> str:
    """마이그레이션이 끝난 테스트 DB URL."""
    return migrated_test_database


@pytest.mark.asyncio
async def test_upsert_round_trip_and_filed_until(db_url: str) -> None:
    engine = create_engine(db_url)
    repo = FundamentalsRepository(create_session_factory(engine))
    try:
        early = fact(
            "revenue",
            date(2024, 1, 1),
            date(2024, 3, 31),
            Decimal(100),
            filed=date(2024, 5, 1),
            accession="R-1",
            form="10-Q",
            fy=2024,
            fp="Q1",
        )
        late = fact(
            "revenue",
            date(2024, 4, 1),
            date(2024, 6, 30),
            Decimal(110),
            filed=date(2024, 8, 1),
            accession="R-2",
            form="10-Q",
            fy=2024,
            fp="Q2",
        )
        assert await repo.upsert_facts([early, late]) == 2
        # 같은 키를 다시 넣으면 값만 갱신 (정정)
        fixed = fact(
            "revenue",
            date(2024, 1, 1),
            date(2024, 3, 31),
            Decimal(101),
            filed=date(2024, 5, 1),
            accession="R-1",
            form="10-Q",
            fy=2024,
            fp="Q1",
        )
        await repo.upsert_facts([fixed])

        everything = await repo.facts_for("TEST")
        assert [f.value for f in everything] == [Decimal(101), Decimal(110)]
        assert everything[0].filed_at == datetime(2024, 5, 1, tzinfo=UTC)
        assert everything[0].period_start == date(2024, 1, 1)

        until = await repo.facts_for("TEST", filed_until=datetime(2024, 6, 1, tzinfo=UTC))
        assert [f.accession for f in until] == ["R-1"]

        listed = await repo.symbols(source="edgar")
        assert ("TEST", datetime(2024, 8, 1, tzinfo=UTC)) in listed
        closes = await repo.daily_closes(
            Market.NASDAQ,
            "TEST",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 12, 31, tzinfo=UTC),
        )
        assert closes == []
    finally:
        await engine.dispose()
