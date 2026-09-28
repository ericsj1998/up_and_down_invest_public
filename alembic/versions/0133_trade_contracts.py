"""몫의 진입 체결 계약 수 — `wf_trades.contracts` (T320 P1 · 2026-09-28).

Note:
    Gate 무기한은 종목당 포지션이 하나라, 같은 방향 다리 여럿이 한 포지션을 나눠 쓰려면(T320)
    **어느 몫이 몇 계약인지** 장부가 알아야 한다 — 거래소는 합계만 알려 준다.
    진입 체결 뒤 한 번 적는다. NULL = 옛 행 · 모형 · 체결 수량을 못 읽은 경우(0 으로 되읽는다).
    더하기만.

Revision ID: 0133_trade_contracts
Revises: 0132_trade_add_json
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0133_trade_contracts"
down_revision: str | None = "0132_trade_add_json"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """몫 계약 수 열을 더한다 (NULL 허용 — 옛 행은 0 으로 되읽는다)."""
    op.add_column("wf_trades", sa.Column("contracts", sa.Integer(), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "contracts")
