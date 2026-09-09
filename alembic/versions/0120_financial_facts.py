"""재무 사실 표 — `financial_facts` (T243 · 2026-09-09).

Note:
    공시에서 온 값 한 칸씩. 지표가 아니라 사실을 저장한다 — 지표 정의가 바뀌어도 다시 받을
    필요가 없다. 키에 `accession` 이 들어가 같은 기간의 여러 공시(원본 · 정정 · 비교 열)가
    전부 남는다 (시점 정합).

Revision ID: 0120_financial_facts
Revises: 0119_market_grants
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0120_financial_facts"
down_revision: str | None = "0119_market_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표 + (symbol, filed_at) 인덱스."""
    op.create_table(
        "financial_facts",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("concept", sa.Text(), primary_key=True),
        sa.Column("unit", sa.Text(), primary_key=True),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column("period_end", sa.Date(), primary_key=True),
        sa.Column("accession", sa.Text(), primary_key=True),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(38, 18), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.Text(), nullable=False),
        sa.Column("form", sa.Text(), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_financial_facts_symbol_filed", "financial_facts", ["symbol", "filed_at"])


def downgrade() -> None:
    """되돌린다 — 다시 받으면 된다."""
    op.drop_index("ix_financial_facts_symbol_filed", table_name="financial_facts")
    op.drop_table("financial_facts")
