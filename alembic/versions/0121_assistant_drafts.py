"""어시스턴트 초안 — `assistant_drafts` (T247 · 2026-09-09).

Note:
    사람마다 한 행(이메일 키). 답은 JSONB 한 칸 — 단계가 늘 때마다 마이그레이션하지 않는다.
    동의(문구 버전·시각)는 여기 열로 두고, 같은 트랜잭션에 `event_logs.consent_given` 을 남긴다.
    계정 저장소(`ACCOUNTS_DATABASE_URL` 이 있으면 그 DB)에 산다 — 사람에게 매인 표라 계정·권한과
    같은 축.

Revision ID: 0121_assistant_drafts
Revises: 0120_financial_facts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0121_assistant_drafts"
down_revision: str | None = "0120_financial_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표 하나."""
    op.create_table(
        "assistant_drafts",
        sa.Column("email", sa.Text(), primary_key=True),
        sa.Column("step", sa.Text(), nullable=False, server_default="consent"),
        sa.Column("answers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("consent_version", sa.Text(), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fund_id", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """되돌린다 — 초안이 사라진다 (동의 이력은 `event_logs` 에 남는다)."""
    op.drop_table("assistant_drafts")
