"""금고 — 전역 설정 표 + 판별 수익선·한계 (T21).

Note:
    🔴 **재충전 상한은 전역이다.** 금고는 하나인데 판마다 다른 상한을 쓰면 *"이 금고가
    얼마나 탔나"* 를 사람이 못 따라간다. 그래서 판 표가 아니라 설정 표에 둔다.

    🔴 **재시작을 넘어야 한다.** 리스크 한도를 메모리에 두면 리로드 한 번에 사라지고,
    사라진 줄 모른 채 계속 돈다.

    ⚠️ 수익선·한계는 **판마다 다르다** — 종목도 배율도 다르니 같은 값을 강요할 이유가
    없다. 그래서 이쪽은 `wf_runs` 에 둔다.

Revision ID: 0104_vault_settings
Revises: 0103_entry_fills
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0104_vault_settings"
down_revision: str | None = "0103_entry_fills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """설정 표를 만들고 판에 금고 칸 둘을 더한다."""
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("key", name="pk_app_settings"),
    )
    # ⚠️ NULL 이 기본이다 — **없으면 지금까지와 똑같이 돈다** (§5.6.2 동결). 값을 넣는
    #    것은 사람의 선택이고, 안 넣었다고 동작이 달라지면 안 된다.
    op.add_column("wf_runs", sa.Column("profit_line", sa.Numeric(38, 18), nullable=True))
    op.add_column("wf_runs", sa.Column("budget_cap", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """되돌린다 — 설정과 금고 칸을 잃는다.

    Note:
        ⚠️ **재충전 상한이 사라진다.** 되돌린 뒤에는 금고가 다시 무한 탄창이 되므로,
        되돌릴 일이 있으면 상한을 다른 방법으로 걸고 나서 한다.
    """
    op.drop_column("wf_runs", "budget_cap")
    op.drop_column("wf_runs", "profit_line")
    op.drop_table("app_settings")
