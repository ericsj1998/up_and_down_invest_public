"""판 종류 — `wf_runs.kind` (T830 · 2026-11-11).

Note:
    판 없이 도는 엔진 다리(나스닥)도 매매를 판 원장(`wf_trades`)에 같은 꼴로 적는다. 원장 줄은
    판 줄에 매달리므로 엔진에도 판 줄이 하나 있어야 하는데, 열린 판 줄은 기동 때 러너로 되살아나고
    증거금 방에도 든다 — 그래서 종류를 적어 그 길들에서 뺀다.
    `session` = 지금까지의 판 전부(기본값) · `engine` = 엔진 다리의 기록 전용 판. 더하기만.

Revision ID: 0137_run_kind
Revises: 0136_universe_snapshots
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0137_run_kind"
down_revision: str | None = "0136_universe_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열을 더한다 — 옛 행은 전부 `session` 이다."""
    op.add_column(
        "wf_runs",
        sa.Column("kind", sa.String(), nullable=False, server_default="session"),
    )


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_runs", "kind")
