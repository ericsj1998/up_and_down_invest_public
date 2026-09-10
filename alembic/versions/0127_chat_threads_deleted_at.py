"""`chat_threads.deleted_at` — 대화 소프트 삭제 (T266 · 2026-09-10).

Note:
    사람에게는 "지움" 이지만 행은 남는다 — 도구 호출·모델 원가가 대화에 묶여 있어 지우면
    리포트 합계가 바뀐다. 목록·열기에서만 숨긴다.

Revision ID: 0127_chat_threads_deleted_at
Revises: 0126_accounts_deleted_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0127_chat_threads_deleted_at"
down_revision: str | None = "0126_accounts_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열을 더한다."""
    op.add_column(
        "chat_threads", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("chat_threads", "deleted_at")
