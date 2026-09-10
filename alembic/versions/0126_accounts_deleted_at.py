"""`accounts.deleted_at` — 계정 소프트 삭제 (T266 · 2026-09-10).

Note:
    계정을 하드 삭제하면 이메일로 매인 권한·토큰·스레드 행이 남고, 같은 이메일이 다시 로그인하면
    새 계정에 옛 권한·토큰이 그대로 붙었다. 이제 지우기는 `deleted_at` + `blocked` 이고, 지우는
    트랜잭션이 토큰을 되돌리고 권한 행을 비운다. 다시 오면 대기 계정으로 되살아난다(권한 0).

Revision ID: 0126_accounts_deleted_at
Revises: 0125_api_tokens
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0126_accounts_deleted_at"
down_revision: str | None = "0125_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열을 더한다."""
    op.add_column("accounts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("accounts", "deleted_at")
