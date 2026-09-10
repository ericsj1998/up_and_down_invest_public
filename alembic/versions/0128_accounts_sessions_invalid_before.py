"""`accounts.sessions_invalid_before` — 로그아웃 서버측 폐기 (T267 #9 · 2026-09-11).

Note:
    세션 쪽지는 서명만으로 12시간을 살았다 — 로그아웃해도 훔친 쿠키는 그대로 통했다.
    로그아웃이 이 시각을 적고, 그 전에 발급된 쪽지는 서명이 맞아도 버린다.

Revision ID: 0128_accounts_sessions_invalid_before
Revises: 0127_chat_threads_deleted_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0128_accounts_sessions_invalid_before"
down_revision: str | None = "0127_chat_threads_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열을 더한다."""
    op.add_column(
        "accounts",
        sa.Column("sessions_invalid_before", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("accounts", "sessions_invalid_before")
