"""거래소 공지 사건 · 받기 상태 — `announcement_events` · `announcement_feeds` (2026-11-28 · T1045).

Note:
    공지 갈래 · 기초 자산 이름마다 가장 이른 공지 한 줄과, 갈래마다 받기 상태(처음 받기 · 마지막
    성공 · 오류 · 관측 칸). 사건 숏 다리의 진입 재료다 — 같은 이름은 한 번만 적는다(재현).

Revision ID: 0140_announcement_events
Revises: 0139_market_breadth
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0140_announcement_events"
down_revision: str | None = "0139_market_breadth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    """표 둘을 만든다."""
    op.create_table(
        "announcement_events",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("announced_at", TS, nullable=False),
        sa.Column("seen_at", TS, nullable=False),
        sa.Column("article_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("rank_symbol", sa.Text(), nullable=True),
        sa.Column("contract", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("kind", "token", name="pk_announcement_events"),
    )
    op.create_index(
        "ix_announcement_events_contract", "announcement_events", ["contract", "announced_at"]
    )
    op.create_table(
        "announcement_feeds",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("backfilled_at", TS, nullable=True),
        sa.Column("ok_at", TS, nullable=True),
        sa.Column("error_at", TS, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("kind", name="pk_announcement_feeds"),
    )


def downgrade() -> None:
    """표 둘을 지운다."""
    op.drop_table("announcement_feeds")
    op.drop_index("ix_announcement_events_contract", table_name="announcement_events")
    op.drop_table("announcement_events")
