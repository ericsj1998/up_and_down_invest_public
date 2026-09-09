"""재무 사실 표 — `financial_facts` (T243 · 2026-09-09)."""

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import Base


class FinancialFactRow(Base):
    """공시에서 온 값 한 칸 — 재계산 가능한 원자료.

    Note:
        지표(PER …)가 아니라 **사실**을 둔다. 지표 정의가 바뀌면 여기서 다시 계산하면 되고, 공시를
        다시 받을 필요가 없다. 같은 기간의 값이 여러 공시(원본 · 정정 · 다음 해 비교 열)에 실리므로
        `accession` 이 키에
        들어간다 — 어느 것을 쓸지는 `filed_at` 으로 읽는 쪽이 고른다 (시점 정합).

        시점 값(재무상태표)은 `period_start == period_end` 다. NULL 을 키에 넣을 수 없어서이기도
        하고,
        "기간 0일" 이 곧 시점 값의 정의이기도 하다.
    """

    __tablename__ = "financial_facts"

    source: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(primary_key=True)
    concept: Mapped[str] = mapped_column(primary_key=True)
    unit: Mapped[str] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    accession: Mapped[str] = mapped_column(primary_key=True)
    entity_id: Mapped[str]
    tag: Mapped[str]
    value: Mapped[Decimal]
    fiscal_year: Mapped[int]
    fiscal_period: Mapped[str]
    form: Mapped[str]
    filed_at: Mapped[datetime]
    fetched_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())

    __table_args__ = (sa.Index("ix_financial_facts_symbol_filed", "symbol", "filed_at"),)
