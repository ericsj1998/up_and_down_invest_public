"""진입 다리 — 사다리 진입의 준비물 (T19 ①).

Note:
    🔴 **`entry` 칸은 그대로 둔다.** SQL 로 훑을 때 평단 하나가 있어야 편하고, 그것은
    이 값에서 나온 **파생값**이다 — 코드가 읽을 때는 다리에서 다시 만든다.

    ⚠️ 빈 객체가 기본값이라 **지금까지의 모든 행이 다리 하나짜리**로 읽힌다. 0.1 과
    옛 기록의 값이 한 비트도 안 달라진다 (§5.6.2 동결).

Revision ID: 0103_entry_fills
Revises: 0102_half_price_and_order_keys
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0103_entry_fills"
down_revision: str | None = "0102_half_price_and_order_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """진입 다리를 적을 칸을 만든다."""
    op.add_column(
        "wf_trades",
        sa.Column(
            "entry_fills_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """되돌린다 — 사다리로 들어간 매매는 평단만 남는다.

    Note:
        ⚠️ **되돌리면 `filled_ratio` 가 1 로 돌아간다.** 다리 하나만 채워졌던 매매의
        손익이 두 배로 계산된다는 뜻이므로, 사다리를 켠 뒤에는 되돌리지 않는다.
    """
    op.drop_column("wf_trades", "entry_fills_json")
