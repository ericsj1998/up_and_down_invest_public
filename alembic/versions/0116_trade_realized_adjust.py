"""재레버 부분 실현 — `wf_trades.realized_adjust` (T229 · 2026-09-09).

Note:
    라이브 러너가 재레버로 포지션을 **줄이면** 거래소는 그 계약의 손익을 그 자리에서 실현하는데,
    원장은 청산까지 아무것도 안 셌다 (NEAR 29→26 · BTC 3→1 실측). 매매마다 실현된 조정 손익(USDT)을
    누적하는 열 하나. NULL = 0 (옛 행). 더하기만 — 블루그린 구간에 안전하다.

Revision ID: 0116_trade_realized_adjust
Revises: 0115_playbook_grants
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0116_trade_realized_adjust"
down_revision: str | None = "0115_playbook_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """조정 실현 열을 더한다 (NULL 허용 — 옛 행은 0 으로 읽는다)."""
    op.add_column("wf_trades", sa.Column("realized_adjust", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "realized_adjust")
