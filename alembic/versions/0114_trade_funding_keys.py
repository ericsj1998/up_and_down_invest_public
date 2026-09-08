"""펀딩 정산 열쇠 — `wf_trades.funding_keys_json` (T226 · 2026-09-08 실측).

Note:
    라이브 러너의 중복 방지 열쇠가 메모리에만 있어 블루그린 승격마다 열린 구간의 정산을 다시
    붙였다 — 원장이 거래소 합의 2.7배(ETH·NEAR·BTC 셋 다). 열쇠를 매매와 함께 적어 재시작이
    몇 번이든 한 번만 붙인다. NULL 허용 · 더하기만 — 옛 이미지(v1.3.0)가 같은 표를 읽는
    블루그린 구간에 안전하다. 열 제거(TODO #3)는 여기 넣지 않는다 — 옛 이미지가 그 열을 읽는다.

Revision ID: 0114_trade_funding_keys
Revises: 0113_edit_delete_caps
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0114_trade_funding_keys"
down_revision: str | None = "0113_edit_delete_caps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """열쇠 열을 더한다 (NULL 허용 — 옛 행은 빈 것으로 읽는다)."""
    op.add_column("wf_trades", sa.Column("funding_keys_json", JSONB(), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "funding_keys_json")
