"""`financial_facts` 저장소 — 재계산 가능한 원자료 (T243).

지표를 저장하지 않고 **사실**을 저장한다. 지표 정의(PER 의 분모를 무엇으로 하나)가 바뀌면 사실에서
다시 계산하면
되지만, 지표만 저장했으면 공시를 다시 받아야 한다. `candles` 저장소와 같은 이유로 raw SQL 이다.

시세는 `candles` 의 일봉 종가를 읽는다 — 재무 지표의 "가격" 은 그 시점의 종가이고, 시점 정합을 위해
`ts <= as_of` 로 자른다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.domain.fundamentals import FinancialFact
from updown.common.domain.instrument import Market, Timeframe

UPSERT_CHUNK_SIZE = 1_000

_UPSERT = sa.text("""
    INSERT INTO financial_facts
        (source, entity_id, symbol, concept, tag, unit, period_start, period_end, value,
         fiscal_year, fiscal_period, form, filed_at, accession)
    VALUES
        (:source, :entity_id, :symbol, :concept, :tag, :unit, :period_start, :period_end, :value,
         :fiscal_year, :fiscal_period, :form, :filed_at, :accession)
    ON CONFLICT (source, symbol, concept, unit, period_start, period_end, accession) DO UPDATE SET
        entity_id = EXCLUDED.entity_id,
        tag = EXCLUDED.tag,
        value = EXCLUDED.value,
        fiscal_year = EXCLUDED.fiscal_year,
        fiscal_period = EXCLUDED.fiscal_period,
        form = EXCLUDED.form,
        filed_at = EXCLUDED.filed_at
""")

_SELECT = """
    SELECT source, entity_id, symbol, concept, tag, unit, period_start, period_end, value,
           fiscal_year, fiscal_period, form, filed_at, accession
    FROM financial_facts
    WHERE symbol = :symbol
"""


class FundamentalsRepositoryError(RuntimeError):
    """영속화 실패."""


class FundamentalsRepository:
    """재무 사실 + 시세 종가 읽기."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """저장소를 만든다.

        Args:
            session_factory: 세션 팩토리 (엔진은 여기서 만들지 않는다).
        """
        self._session_factory = session_factory

    async def upsert_facts(self, facts: Sequence[FinancialFact]) -> int:
        """사실을 upsert 한다 — 같은 공시의 같은 기간은 값만 갱신.

        Args:
            facts: 사실.

        Returns:
            시도한 행 수.

        Raises:
            FundamentalsRepositoryError: 적재 실패.
        """
        if not facts:
            return 0
        rows = [
            {
                "source": f.source,
                "entity_id": f.entity_id,
                "symbol": f.symbol,
                "concept": f.concept,
                "tag": f.tag,
                "unit": f.unit,
                "period_start": f.period_start,
                "period_end": f.period_end,
                "value": f.value,
                "fiscal_year": f.fiscal_year,
                "fiscal_period": f.fiscal_period,
                "form": f.form,
                "filed_at": f.filed_at,
                "accession": f.accession,
            }
            for f in facts
        ]
        try:
            async with self._session_factory() as session:
                for start in range(0, len(rows), UPSERT_CHUNK_SIZE):
                    await session.execute(_UPSERT, rows[start : start + UPSERT_CHUNK_SIZE])
                await session.commit()
        except Exception as exc:
            raise FundamentalsRepositoryError(
                f"재무 사실 적재 실패({facts[0].symbol}): {exc}"
            ) from exc
        return len(rows)

    async def facts_for(
        self, symbol: str, *, filed_until: datetime | None = None
    ) -> list[FinancialFact]:
        """종목의 사실을 읽는다.

        Args:
            symbol: 종목.
            filed_until: 이 시각까지 공시된 것만 (포함). None 이면 전부. 시점 정합의 1차 필터 —
                공시 당일 가용 지연은 `analysis.fundamentals.series.known_facts` 가 다시 건다.

        Returns:
            공시일 오름차순.
        """
        statement = _SELECT + (" AND filed_at <= :until" if filed_until is not None else "")
        statement += " ORDER BY filed_at, period_end, concept"
        params: dict[str, object] = {"symbol": symbol}
        if filed_until is not None:
            params["until"] = filed_until
        async with self._session_factory() as session:
            rows = (await session.execute(sa.text(statement), params)).all()
        return [
            FinancialFact(
                source=row.source,
                entity_id=row.entity_id,
                symbol=row.symbol,
                concept=row.concept,
                tag=row.tag,
                unit=row.unit,
                period_start=row.period_start,
                period_end=row.period_end,
                value=Decimal(row.value),
                fiscal_year=row.fiscal_year,
                fiscal_period=row.fiscal_period,
                form=row.form,
                filed_at=row.filed_at,
                accession=row.accession,
            )
            for row in rows
        ]

    async def symbols(self, source: str | None = None) -> list[tuple[str, datetime]]:
        """사실이 있는 종목과 마지막 공시일.

        Args:
            source: 출처로 거른다. None 이면 전부.

        Returns:
            `(symbol, 마지막 filed_at)` 종목 순.
        """
        statement = "SELECT symbol, MAX(filed_at) AS latest FROM financial_facts"
        params: dict[str, object] = {}
        if source is not None:
            statement += " WHERE source = :source"
            params["source"] = source
        statement += " GROUP BY symbol ORDER BY symbol"
        async with self._session_factory() as session:
            rows = (await session.execute(sa.text(statement), params)).all()
        return [(row.symbol, row.latest) for row in rows]

    async def instruments(self, market: Market) -> list[str]:
        """그 시장의 종목 코드 — 저평가 후보의 유니버스 (T244 · T238 시장).

        Args:
            market: 시장.

        Returns:
            종목 코드 오름차순.
        """
        statement = sa.text("SELECT symbol FROM instruments WHERE market = :market ORDER BY symbol")
        async with self._session_factory() as session:
            rows = (await session.execute(statement, {"market": market.value})).scalars().all()
        return [str(row) for row in rows]

    async def daily_closes(
        self, market: Market, symbol: str, start: datetime, end: datetime
    ) -> list[tuple[date, Decimal]]:
        """일봉 종가 (시점 정합용 가격 역사).

        Args:
            market: 시장.
            symbol: 종목.
            start: 시작 (포함).
            end: 끝 (포함).

        Returns:
            `(날짜, 종가)` 오름차순. 봉이 없으면 빈 목록 — 가격 없이 가격 지표를 지어내지 않는다.
        """
        statement = sa.text("""
            SELECT c.ts, c.close
            FROM candles c JOIN instruments i ON i.id = c.instrument_id
            WHERE i.market = :market AND i.symbol = :symbol AND c.timeframe = :timeframe
              AND c.ts >= :start AND c.ts <= :end
            ORDER BY c.ts
        """)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    statement,
                    {
                        "market": market.value,
                        "symbol": symbol,
                        "timeframe": Timeframe.D1.value,
                        "start": start,
                        "end": end,
                    },
                )
            ).all()
        return [(row.ts.date(), Decimal(row.close)) for row in rows]


__all__ = ["FundamentalsRepository", "FundamentalsRepositoryError"]
