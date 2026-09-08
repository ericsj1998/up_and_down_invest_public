"""계정 `demo_trade` — 열람자에게 **테스트넷 주문만** 허용하는 토글 (사용자 2026-09-07).

Note:
    실계좌·데모 API 가 계정 표를 함께 쓰게 되면서(`ACCOUNTS_DATABASE_URL`) "데모 거래만" 은
    등급이 아니라 별도 플래그다 — 등급 하나에 서버마다 다른 뜻을 주면 표가 둘이던 때와 같다.
    열람자 + demo_trade = 데모 서버에서만 거래 · 거래자 = 양쪽.

Revision ID: 0111_account_demo_trade
Revises: 0110_account_hold
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0111_account_demo_trade"
down_revision: str | None = "0110_account_hold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """데모 거래 플래그 열을 더한다 (기본 false)."""
    op.add_column(
        "accounts",
        sa.Column("demo_trade", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """데모 거래 플래그 열을 뺀다."""
    op.drop_column("accounts", "demo_trade")
