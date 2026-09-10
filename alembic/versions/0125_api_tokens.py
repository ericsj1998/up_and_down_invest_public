"""`api_tokens` — 개인 API 토큰 (T263 MCP · 2026-09-10).

Note:
    구글 로그인 쪽지는 브라우저 쿠키라 Claude Desktop 같은 MCP 클라이언트가 못 쓴다. 사람이
    화면에서 만든 토큰(`updn_…`)을 `Authorization: Bearer` 로 보내면 미들웨어가 그 사람으로 본다 —
    단 **읽기와 MCP 만**(`Caller.via_token`). 값은 저장하지 않고 SHA-256 해시만 둔다. 되돌리기는
    `revoked_at`.

Revision ID: 0125_api_tokens
Revises: 0124_instruments_market_width
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0125_api_tokens"
down_revision: str | None = "0124_instruments_market_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표를 만든다."""
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """표를 지운다 — 토큰은 되살릴 수 없다(해시뿐)."""
    op.drop_table("api_tokens")
