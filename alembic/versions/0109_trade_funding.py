"""매매별 펀딩 누적 — `wf_trades.funding_paid` · `funding_pct` (T226 · 사용자 2026-09-07).

Note:
    무기한 선물의 8시간 펀딩이 원장에 없어 보유가 길어도 비용이 늘지 않았다. 라이브는 거래소
    정산 기록을, 백테스트·페이퍼는 정산 경계 모형을 매매에 붙인다. 옛 행은 NULL = 0
    (하위 호환 · 성적이 바뀌지 않는다).

Revision ID: 0109_trade_funding
Revises: 0108_account_audit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0109_trade_funding"
down_revision: str | None = "0108_account_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """펀딩 누적 두 열을 더한다 (NULL 허용 — 옛 행은 0 으로 읽는다)."""
    op.add_column("wf_trades", sa.Column("funding_paid", sa.Numeric(38, 18), nullable=True))
    op.add_column("wf_trades", sa.Column("funding_pct", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """두 열을 뺀다."""
    op.drop_column("wf_trades", "funding_pct")
    op.drop_column("wf_trades", "funding_paid")
