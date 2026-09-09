"""`instruments.market` 열 폭 — VARCHAR(6) → VARCHAR(16) (T251 · 2026-09-10).

Note:
    0001 이 `sa.Enum(..., native_enum=False)` 로 만들 때 폭이 그때 가장 긴 값(6)으로 잡혔다.
    그 뒤 `Market` 에 `BINANCE`(7) 가 들어와 그 시장의 종목이 `instruments` 에 못 들어갔다.
    네이티브 ENUM 이 아니라 그냥 VARCHAR 라 폭만 넓히면 된다. 열거형 값을 CHECK 로 묶지
    않으므로(`create_constraint=False`) 제약 변경은 없다. 앞으로 값을 더해도 16 안이면
    마이그레이션이 필요 없다.

Revision ID: 0124_instruments_market_width
Revises: 0123_ai_participants
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0124_instruments_market_width"
down_revision: str | None = "0123_ai_participants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WIDTH = 16


def upgrade() -> None:
    """폭을 넓힌다 — 데이터는 그대로."""
    op.alter_column(
        "instruments",
        "market",
        type_=sa.String(WIDTH),
        existing_type=sa.String(6),
        existing_nullable=False,
    )


def downgrade() -> None:
    """되돌린다 — 7자 이상 값이 있으면 실패한다(그 행을 먼저 지운다)."""
    op.alter_column(
        "instruments",
        "market",
        type_=sa.String(6),
        existing_type=sa.String(WIDTH),
        existing_nullable=False,
    )
