"""매매에 실제로 건 증거금 — `wf_trades.margin_used` (T285 · 2026-09-17).

Note:
    펀드 멤버 원장은 손익을 `증거금 x 손익률` 로 세는데, 멤버의 증거금은 펀드가 틱마다 다시 주는
    예산이라 걷기 시작점(시드)과 달랐다. 그래서 예산이 바뀔 때마다 과거 손익이 새 예산 기준으로
    다시 계산돼 펀드 총자본이 샜다(로컬 데모 -94% · 실계좌 장부 300 → 132 · 실잔고 298 그대로).
    주문 시점의 예산을 여기 적어 그 매매의 손익을 고정한다. NULL = 옛 행 · 백테스트 · 단독 판
    (걷기 증거금으로 센다 · 값 그대로). 더하기만.

Revision ID: 0130_trade_margin_used
Revises: 0129_drop_dead_tables
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0130_trade_margin_used"
down_revision: str | None = "0129_drop_dead_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """실제 증거금 열을 더한다 (NULL 허용 — 옛 행은 걷기 증거금 그대로)."""
    op.add_column("wf_trades", sa.Column("margin_used", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "margin_used")
