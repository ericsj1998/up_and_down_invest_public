"""계약 손익 - 의도 손익 — `wf_trades.contract_adjust` (T606 · 2026-10-28).

Note:
    원장 손익은 의도 배율(`leverage`)로, 거래소 손익은 실제 계약으로 셈해 정수 반올림 · 부분 체결 ·
    이어받기에서 갈렸다. 라이브 러너가 청산 뒤 그 차를 매매마다 적고 걷기가 더한다 —
    장부 몫 = 거래소 몫.
    NULL = 옛 행 · 모형(0 으로 되읽는다). 더하기만.

Revision ID: 0135_trade_contract_adjust
Revises: 0134_playbook_decls
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0135_trade_contract_adjust"
down_revision: str | None = "0134_playbook_decls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열을 더한다 (NULL 허용 — 옛 행은 0 으로 되읽는다)."""
    op.add_column("wf_trades", sa.Column("contract_adjust", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "contract_adjust")
