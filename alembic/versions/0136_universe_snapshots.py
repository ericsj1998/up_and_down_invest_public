"""분기 우주 스냅샷 — `universe_snapshots` (T758 · 2026-11-04).

Note:
    분기마다 한 번 계산한 다리별 종목 목록의 불변 기록. 실패 · 이전 목록 유지도 행으로 남는다.
    🔴 UPDATE · DELETE 는 트리거로 막는다 — 지난 분기에 무엇으로 매매했는지가 바뀌면 재현이 안 된다.
    같은 분기 · 프로필 · 씨앗 · 규칙의 `ok` 행은 하나뿐이다(부분 고유 색인).

Revision ID: 0136_universe_snapshots
Revises: 0135_trade_contract_adjust
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0136_universe_snapshots"
down_revision: str | None = "0135_trade_contract_adjust"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """표 · 색인 · 불변 트리거를 만든다."""
    op.create_table(
        "universe_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("quarter", sa.Date(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("seed_tag", sa.String(), nullable=False),
        sa.Column("rule_digest", sa.String(), nullable=False),
        sa.Column("input_digest", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False, server_default=""),
        sa.Column("ranked", JSONB(), nullable=False),
        sa.Column("alive", JSONB(), nullable=False),
        sa.Column("boundary_gaps", JSONB(), nullable=False),
        sa.Column("legs", JSONB(), nullable=False),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_universe_snapshots_quarter", "universe_snapshots", ["quarter"])
    op.create_index(
        "uq_universe_snapshots_ok",
        "universe_snapshots",
        ["quarter", "profile", "seed_tag", "rule_digest"],
        unique=True,
        postgresql_where=sa.text("status = 'ok'"),
    )
    op.execute(
        """
        CREATE FUNCTION universe_snapshots_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'universe_snapshots 는 더하기만 한다 (T758)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER universe_snapshots_no_change BEFORE UPDATE OR DELETE ON universe_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION universe_snapshots_immutable();"
    )


def downgrade() -> None:
    """표 · 트리거 · 함수를 지운다."""
    op.execute("DROP TRIGGER IF EXISTS universe_snapshots_no_change ON universe_snapshots;")
    op.drop_table("universe_snapshots")
    op.execute("DROP FUNCTION IF EXISTS universe_snapshots_immutable();")
