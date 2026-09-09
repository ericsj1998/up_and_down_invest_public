"""실제 수수료 — `wf_trades.fee_actual` (T236 · 2026-09-09).

Note:
    원장은 왕복 비용을 모형(`cost_pct` · costs.yml 테이커 왕복 0.15%)으로 뺐는데 거래소는
    실제 요율(메이커 0.02% · 테이커 0.05% 등 계정별)로 뗀다. 작은 매매에서 그 차이가 부호를
    뒤집었다(데모 DOGE
    원장 -0.40 vs 거래소 +0.25). 청산 이력(`position_close.pnl_fee`)에서 읽은 실제 수수료
    (USDT · 양수)를 적고, `cost_pct` 를 그 비율로 바꾼다. NULL = 아직 안 맞춤 (옛 행 · 모형값
    그대로). 더하기만.

Revision ID: 0117_trade_fee_actual
Revises: 0116_trade_realized_adjust
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0117_trade_fee_actual"
down_revision: str | None = "0116_trade_realized_adjust"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """실제 수수료 열을 더한다 (NULL 허용 — 옛 행은 모형 비용 그대로)."""
    op.add_column("wf_trades", sa.Column("fee_actual", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "fee_actual")
