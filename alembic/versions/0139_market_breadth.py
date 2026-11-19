"""시장 전체 값의 재료 · 결과 — `market_daily_closes` · `market_breadth_days` (2026-11-19).

Note:
    전 종목 UTC 일봉 종가(하루 한 번 쌓음)와 그것으로 잰 그날의 시장 전체 값(종목 폭 · 뒤처진 묶음
    수익). 진입 문이 읽는 사실 입력이다 — 같은 칸 · 같은 줄은 한 번만 적는다(재현).

Revision ID: 0139_market_breadth
Revises: 0138_trade_initial_stop
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0139_market_breadth"
down_revision: str | None = "0138_trade_initial_stop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 18)


def upgrade() -> None:
    """표 둘을 만든다."""
    op.create_table(
        "market_daily_closes",
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("close", MONEY, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("market", "symbol", "day", name="pk_market_daily_closes"),
    )
    op.create_index("ix_market_daily_closes_day", "market_daily_closes", ["market", "day"])
    op.create_table(
        "market_breadth_days",
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ma_days", sa.Integer(), nullable=False),
        sa.Column("lag_days", sa.Integer(), nullable=False),
        sa.Column("rank_days", sa.Integer(), nullable=False),
        sa.Column("breadth", MONEY, nullable=True),
        sa.Column("lag_ret", MONEY, nullable=True),
        sa.Column("breadth_count", sa.Integer(), nullable=False),
        sa.Column("lag_count", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "market", "day", "ma_days", "lag_days", "rank_days", name="pk_market_breadth_days"
        ),
    )


def downgrade() -> None:
    """표 둘을 지운다."""
    op.drop_table("market_breadth_days")
    op.drop_index("ix_market_daily_closes_day", table_name="market_daily_closes")
    op.drop_table("market_daily_closes")
