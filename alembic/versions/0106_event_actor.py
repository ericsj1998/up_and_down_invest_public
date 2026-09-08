"""감사 로그에 **누가 했는지** 남긴다 (2026-08-30 외부 공개 준비).

Note:
    🔴 `trace_id` 는 한 흐름을 잇지만 그 흐름을 **누가 시작했는지**는 안 말해 준다.
    사람이 여럿 들어오는 순간 *"이 주문 누가 냈지"* 를 답할 수 있어야 한다.

    ⛔ **NULL 을 허용한다** — 엔진의 스케줄 잡·부팅 로그는 사람이 시작한 것이 아니다.
    NOT NULL 로 두면 거기에 가짜 값을 채우게 되고, 그건 "누가 했는지 아는 척" 이다.

    ⚠️ **외래키를 안 건다.** 감사 기록은 그때의 사실이고, 나중에 계정을 지운다고
    기록에서 사람이 사라지면 안 된다 (규칙 8-2 의 정신 — 감사 행은 안 지운다).

    ⚠️ 옛 행은 NULL 로 남는다. 인증이 없던 시절의 기록이므로 그것이 정직한 값이다 —
    소급해서 채우지 않는다.

Revision ID: 0106_event_actor
Revises: 0105_accounts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0106_event_actor"
down_revision: str | None = "0105_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """`event_logs.actor` 를 더한다."""
    op.add_column("event_logs", sa.Column("actor", sa.String(), nullable=True))
    # ⭐ *"이 사람이 무엇을 했나"* 가 이 컬럼의 주 질의다 — 색인이 없으면 전체 훑기다.
    op.create_index("ix_event_logs_actor", "event_logs", ["actor"])


def downgrade() -> None:
    """되돌린다 — ⚠️ 누가 했는지가 통째로 사라진다."""
    op.drop_index("ix_event_logs_actor", table_name="event_logs")
    op.drop_column("event_logs", "actor")
