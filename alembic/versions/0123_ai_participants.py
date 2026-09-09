"""AI 토너먼트 참가자 — `ai_participants` (T249 · 2026-09-10).

Note:
    참가자 = 모델 x 프롬프트 버전 x 프롬프트 해시 x 스냅샷 설정. 행은 **한 번 얼면 안 바뀐다**
    (`frozen_at`) —
    성과는 판 메타 `ai.participant` 로 이 키에 귀속된다. 계정 저장소에 산다(채팅 표와 같은 곳).

Revision ID: 0123_ai_participants
Revises: 0122_chat_threads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0123_ai_participants"
down_revision: str | None = "0122_chat_threads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표 하나."""
    op.create_table(
        "ai_participants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """되돌린다 — 성과 귀속은 판 메타에 남아 있어 표는 다시 만들 수 있다."""
    op.drop_table("ai_participants")
