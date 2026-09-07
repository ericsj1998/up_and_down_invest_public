"""반익 체결가 칸 + 주문 표의 쓰기 순서 의존 제거 (2026-08-19 사고 ③④).

Note:
    🔴 **두 결함이 같은 마이그레이션에 있는 이유**: 사고 조사 뒤 `wf_runs` 를 통째로
    비웠으므로 이관할 행이 없다. 나눠 두면 빈 표를 두 번 흔들 뿐이다.

    ⚠️ `wf_orders` 는 **되짚기용 표**다. 데이터를 버리는 것이 아깝지 않은 이유는,
    이 표가 사고 당시 **거래소 체결 77건 중 29건만** 담고 있었기 때문이다 — 남은 것도
    전부 손절 행이었다.

Revision ID: 0102_half_price_and_order_keys
Revises: 0101_walkforward_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102_half_price_and_order_keys"
down_revision: str | None = "0101_walkforward_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """반익 체결가를 적을 칸을 만들고, 주문을 판에 직접 매단다."""
    # ── ③ 반익을 얼마에 덜었나 ────────────────────────────────
    #
    # 🔴 이 칸이 없어서 `exit_average` 가 **언제나 계획가**로 계산했다. 전환 신호 반익은
    #    그 가격에 닿은 적이 없어, 28건이 전부 이긴 매매로 세어졌다 (실제로는 1건).
    #
    # ⚠️ NULL 을 허용한다 — 옛 행은 *"그때는 계획가로 쟀다"* 는 사실을 그대로 남긴다.
    op.add_column("wf_trades", sa.Column("half_price", sa.Numeric(38, 18), nullable=True))

    # ── ④ 주문의 쓰기 순서 의존을 끊는다 ─────────────────────
    #
    # 🔴 `trade_row_id → wf_trades.id` 외래키가 *"매매를 먼저 저장해야 주문을 적을 수
    #    있다"* 는 제약을 만들었다. 러너의 걸음은 주문 → 저장 순서라 **진입 주문은 늘
    #    부모가 없는 시점에 왔고 조용히 버려졌다.**
    #
    # ⭐ 판에 직접 매달면 순서가 사라지고, 부모 조회가 없어져 DB 왕복도 2회 → 1회다.
    op.drop_table("wf_orders")
    op.create_table(
        "wf_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trade_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exchange_order_id", sa.Text(), nullable=False),
        sa.Column("contracts", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["run_id"], ["wf_runs.id"], name="fk_wf_orders_run_id_wf_runs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wf_orders"),
        sa.UniqueConstraint("run_id", "trade_id", "role", name="uq_wf_orders_run_trade_role"),
    )
    op.create_index("ix_wf_orders_run_id", "wf_orders", ["run_id"])
    op.create_index("ix_wf_orders_trade_id", "wf_orders", ["trade_id"])


def downgrade() -> None:
    """되돌린다 — 주문 행은 잃는다.

    Note:
        ⚠️ **이관하지 않는다.** 옛 구조는 매매 행이 있어야만 주문을 담을 수 있는데,
        새 구조에는 매매 없는 주문이 정상적으로 들어 있다. 그것을 억지로 밀어 넣으면
        되짚기 표가 조용히 줄어들고, 그게 바로 이 마이그레이션이 고친 결함이다.
    """
    op.drop_index("ix_wf_orders_trade_id", table_name="wf_orders")
    op.drop_index("ix_wf_orders_run_id", table_name="wf_orders")
    op.drop_table("wf_orders")
    op.create_table(
        "wf_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trade_row_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exchange_order_id", sa.Text(), nullable=False),
        sa.Column("contracts", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["trade_row_id"],
            ["wf_trades.id"],
            name="fk_wf_orders_trade_row_id_wf_trades",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wf_orders"),
        sa.UniqueConstraint("trade_row_id", "role", name="uq_wf_orders_trade_role"),
    )
    op.create_index("ix_wf_orders_trade_row_id", "wf_orders", ["trade_row_id"])
    op.drop_column("wf_trades", "half_price")
