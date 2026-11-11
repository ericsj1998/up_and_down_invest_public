"""처음 손절 — `wf_trades.initial_stop` (T820 · 2026-11-11).

Note:
    예상 청산 띠 · 앞날 부채꼴은 "처음 손절까지 거리 = 1R" 로 잰다. `planned_stop` 은
    트레일 · 본전 이동으로 덮어써져 처음 값이 남지 않았다.
    표시 전용 · 더하기만 · 옛 행은 NULL(처음 값을 모름).

Revision ID: 0138_trade_initial_stop
Revises: 0137_run_kind
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0138_trade_initial_stop"
down_revision: str | None = "0137_run_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열을 더한다 — 옛 행은 NULL."""
    op.add_column("wf_trades", sa.Column("initial_stop", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "initial_stop")
