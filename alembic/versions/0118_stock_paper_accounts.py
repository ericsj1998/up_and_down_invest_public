"""주식 페이퍼 계좌 — `stock_paper_accounts` (T240 · 2026-09-09).

Note:
    토스에는 테스트넷이 없어 체결·잔고를 우리가 모의한다(`execution/stock_paper.py`). 토스 실주문이
    당분간 범위 밖이라(사용자 2026-09-09) 이 계좌가 곧 주식 운영 계좌다 — 파일이 아니라 백업이 도는
    DB 에 둔다. 시장 하나에 JSONB 한 행(현금·포지션·대기 주문·조건부·마감·장부).

Revision ID: 0118_stock_paper_accounts
Revises: 0117_trade_fee_actual
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0118_stock_paper_accounts"
down_revision: str | None = "0117_trade_fee_actual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표를 만든다."""
    op.create_table(
        "stock_paper_accounts",
        sa.Column("market", sa.String(), primary_key=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """표를 지운다 — 페이퍼 계좌가 사라진다."""
    op.drop_table("stock_paper_accounts")
