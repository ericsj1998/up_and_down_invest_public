"""모의 라이브 판 영속화 — `wf_runs` · `wf_trades` · `wf_orders` (T16 ②).

🔴 **판이 재시작에 죽는 것을 막는다.** 저널은 세션 id 가 곧 파일명이라 리로드마다 새
파일이 생기고 옛 파일은 고아가 됐다. 그러면 거래소 포지션은 남는데 원장이 사라져서
반익·본절 상향·손절 재장착이 전부 멈춘다.

⭐ **닻은 A 안**(종목 + 매매법 + live)이고, 그 위험은 **부분 유니크 인덱스**로 막는다 —
`anchor` 는 `closed_at IS NULL` 인 행에서만 유일하다. 열린 판은 닻마다 하나뿐이고,
닫힌 판은 몇 개든 남아 과거 성적이 보존된다.

⚠️ `downgrade` 는 표를 지운다. 판 기록이 통째로 사라지므로 운영에서 부르지 않는다 —
왕복 테스트(P0-4 DoD 5)를 위해 존재한다.

Revision ID: 0101_walkforward_runs
Revises: 0100_timeframe_expand
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0101_walkforward_runs"
down_revision: str | None = "0100_timeframe_expand"
branch_labels: str | None = None
depends_on: str | None = None

MONEY = sa.Numeric(38, 18)
RATIO = sa.Numeric(18, 8)


def upgrade() -> None:
    """세 표를 만든다."""
    op.create_table(
        "wf_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("anchor", sa.Text(), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("playbook", sa.Text(), nullable=False),
        sa.Column("playbook_id", sa.Text(), nullable=False),
        sa.Column("seed_cash", MONEY, nullable=False),
        sa.Column("margin_budget", MONEY, nullable=True),
        sa.Column("leverage", RATIO, nullable=False),
        sa.Column("skim_pct", RATIO, nullable=False),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("meta_json", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wf_runs"),
    )
    op.create_index("ix_wf_runs_key", "wf_runs", ["key"], unique=True)
    op.create_index("ix_wf_runs_anchor", "wf_runs", ["anchor"])
    # 🔴 **A 안의 안전장치.** 열린 판은 닻마다 하나뿐 — 두 판이 같은 종목을 돌면
    #    Gate 는 종목당 포지션이 하나라 서로의 포지션을 자기 것으로 여긴다.
    op.create_index(
        "uq_wf_runs_open_anchor",
        "wf_runs",
        ["anchor"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    op.create_table(
        "wf_trades",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trade_id", sa.Text(), nullable=False),
        sa.Column("playbook", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("placed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("half_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("half_by", sa.Text(), nullable=True),
        sa.Column("entry", MONEY, nullable=False),
        sa.Column("exit_price", MONEY, nullable=True),
        sa.Column("planned_stop", MONEY, nullable=False),
        sa.Column("planned_first", MONEY, nullable=False),
        sa.Column("planned_target", MONEY, nullable=False),
        sa.Column("cost_pct", RATIO, nullable=False),
        sa.Column("leverage", RATIO, nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("evidence_json", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["wf_runs.id"],
            name="fk_wf_trades_run_id_wf_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wf_trades"),
        sa.UniqueConstraint("run_id", "trade_id", name="uq_wf_trades_run_trade"),
    )
    op.create_index("ix_wf_trades_run_id", "wf_trades", ["run_id"])
    op.create_index("ix_wf_trades_trade_id", "wf_trades", ["trade_id"])

    op.create_table(
        "wf_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trade_row_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exchange_order_id", sa.Text(), nullable=False),
        sa.Column("contracts", sa.Text(), nullable=False),
        sa.Column("price", MONEY, nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("raw_json", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trade_row_id"],
            ["wf_trades.id"],
            name="fk_wf_orders_trade_row_id_wf_trades",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wf_orders"),
        # 🔴 역할 하나에 행 하나 — 조건부 손절은 24시간에 만료돼 매 걸음 다시 걸린다.
        #    걸 때마다 쌓으면 하루 수천 행이 되고 "지금 걸려 있나" 를 못 읽는다.
        sa.UniqueConstraint("trade_row_id", "role", name="uq_wf_orders_trade_role"),
    )
    op.create_index("ix_wf_orders_trade_row_id", "wf_orders", ["trade_row_id"])


def downgrade() -> None:
    """세 표를 지운다.

    Note:
        ⛔ 운영에서 부르지 않는다 — 판 기록이 통째로 사라진다. 왕복 테스트용이다.
    """
    op.drop_table("wf_orders")
    op.drop_table("wf_trades")
    op.drop_table("wf_runs")
