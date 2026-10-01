"""DB 저장 매매법 선언 — `playbook_decls` (T349 · 2026-10-02).

Note:
    재배포 없이 매매법을 더하려고 사람이 API 로 넣는 선언. 파일(`config/playbooks.yml`)이 SSoT 이고
    이 표는 덧붙이기(같은 id 는 파일이 이긴다). 더하기만.

Revision ID: 0134_playbook_decls
Revises: 0133_trade_contracts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0134_playbook_decls"
down_revision: str | None = "0133_trade_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표를 만든다."""
    op.create_table(
        "playbook_decls",
        sa.Column("playbook_id", sa.String(), primary_key=True),
        sa.Column("body", JSONB(), nullable=False),
        sa.Column("basket", JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """표를 지운다."""
    op.drop_table("playbook_decls")
