"""AI 채팅 대화 — `chat_threads` (T248 · 2026-09-09).

Note:
    사람마다 여러 대화. 메시지는 JSONB 목록 — 역할·본문·도구 호출·근거·제안. 도구 호출의 감사 기록은
    `event_logs.ai_chat_turn`(추가만). 계정 저장소에 산다(사람에게 매인 표).

Revision ID: 0122_chat_threads
Revises: 0121_assistant_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0122_chat_threads"
down_revision: str | None = "0121_assistant_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표 + 이메일 인덱스."""
    op.create_table(
        "chat_threads",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("messages", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_threads_email", "chat_threads", ["email"])


def downgrade() -> None:
    """되돌린다 — 대화가 사라진다(도구 호출 감사는 `event_logs` 에 남는다)."""
    op.drop_index("ix_chat_threads_email", table_name="chat_threads")
    op.drop_table("chat_threads")
