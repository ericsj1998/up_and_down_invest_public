"""시장별 권한 — `role_collections.market_policy` + `market_grants` (T242 · 2026-09-09).

Note:
    T230 의 매매법 축(0115) 옆에 시장 갈래 축(코인 · 국내주식 · 미국주식).
    묶음 기본값은 JSONB 한 칸, 사람별 덮어쓰기는 행 하나(이메일 x 갈래).
    NULL 정책 = 내장값(`security.markets.BUILTIN_MARKET_POLICIES`).

Revision ID: 0119_market_grants
Revises: 0118_stock_paper_accounts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0119_market_grants"
down_revision: str | None = "0118_stock_paper_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """정책 열 + 덮어쓰기 표."""
    op.add_column("role_collections", sa.Column("market_policy", JSONB(), nullable=True))
    op.create_table(
        "market_grants",
        sa.Column("email", sa.String(), primary_key=True),
        sa.Column("market_group", sa.String(), primary_key=True),
        sa.Column("view", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("backtest", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trade", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("granted_by", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
    )
    op.create_index("ix_market_grants_email", "market_grants", ["email"])


def downgrade() -> None:
    """되돌린다 — 덮어쓰기 행이 사라진다."""
    op.drop_index("ix_market_grants_email", table_name="market_grants")
    op.drop_table("market_grants")
    op.drop_column("role_collections", "market_policy")
