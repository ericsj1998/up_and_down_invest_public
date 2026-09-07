"""계정 보류·메모·관리자 문의 (사용자 2026-09-07).

Note:
    승인 대기가 하루를 넘기면 보류된다(`standing.py`). 관리자가 기한을 두고 풀 수 있는 열
    (`hold_released_until`), 계정별 메모(`note`), 마지막 문의 시각(`contacted_at`) 을 더하고,
    보류된 사람이 보낸 문의를 `account_contacts` 에 남긴다 — 메일이 못 가도 관리자 화면에는 뜬다.

Revision ID: 0110_account_hold
Revises: 0109_trade_funding
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0110_account_hold"
down_revision: str | None = "0109_trade_funding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """계정 열 셋 + 문의 표."""
    op.add_column(
        "accounts", sa.Column("hold_released_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("accounts", sa.Column("note", sa.String(), nullable=False, server_default=""))
    op.add_column("accounts", sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "account_contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("mailed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by", sa.String(), nullable=False, server_default=""),
    )
    op.create_index("ix_account_contacts_email", "account_contacts", ["email"])


def downgrade() -> None:
    """되돌린다."""
    op.drop_index("ix_account_contacts_email", table_name="account_contacts")
    op.drop_table("account_contacts")
    op.drop_column("accounts", "contacted_at")
    op.drop_column("accounts", "note")
    op.drop_column("accounts", "hold_released_until")
