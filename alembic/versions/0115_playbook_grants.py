"""매매법별 권한 — `role_collections.playbook_policy` · `playbook_grants` (T230 · 2026-09-08).

Note:
    권한 단위가 기능(T228)에서 기능 x 매매법으로 넓어진다. 묶음은 정책(칸마다 "전부" 또는 목록)을
    갖고, 사람마다 매매법 하나씩 덮어쓸 수 있다. 이력은 `event_logs` 의 `permission_changed`.

    내장 묶음 기본값(사용자 확정): 게스트 = 모든 매매법 보기 · 견본 백테스트 · 거래(데모).
    열람자/실거래 조회 게스트 = 보기 + 견본 백테스트. 거래자 이상 = 전부. 이미 적은 정책은 그대로.

    더하기만 — 옛 이미지(v1.3.x)가 같은 표를 읽는 블루그린 구간에 안전하다.

Revision ID: 0115_playbook_grants
Revises: 0114_trade_funding_keys
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0115_playbook_grants"
down_revision: str | None = "0114_trade_funding_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ⚠️ 마이그레이션은 코드의 상수를 import 하지 않는다 — 그날의 값이 그대로 남아야 한다.
_SAMPLE = ["sample_ma_cross"]
_ALL = "*"
_POLICIES: dict[str, dict[str, object]] = {
    "guest": {"view": _ALL, "backtest": _SAMPLE, "trade": _ALL},
    "live_watch_guest": {"view": _ALL, "backtest": _SAMPLE, "trade": []},
    "viewer": {"view": _ALL, "backtest": _SAMPLE, "trade": []},
    "trader": {"view": _ALL, "backtest": _ALL, "trade": _ALL},
    "admin": {"view": _ALL, "backtest": _ALL, "trade": _ALL},
    "super_admin": {"view": _ALL, "backtest": _ALL, "trade": _ALL},
}


def upgrade() -> None:
    """정책 열 · 덮어쓰기 표 · 내장 묶음 기본값."""
    op.add_column("role_collections", sa.Column("playbook_policy", JSONB(), nullable=True))
    op.create_table(
        "playbook_grants",
        sa.Column("email", sa.String(), primary_key=True),
        sa.Column("playbook_id", sa.String(), primary_key=True),
        sa.Column("view", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("backtest", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trade", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("granted_by", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
    )
    op.create_index("ix_playbook_grants_email", "playbook_grants", ["email"])
    conn = op.get_bind()
    for name, policy in _POLICIES.items():
        conn.execute(
            sa.text(
                "UPDATE role_collections SET playbook_policy = CAST(:policy AS jsonb), "
                "updated_by = '0115' WHERE name = :name AND playbook_policy IS NULL"
            ),
            {"name": name, "policy": json.dumps(policy)},
        )


def downgrade() -> None:
    """표와 열을 뺀다."""
    op.drop_index("ix_playbook_grants_email", table_name="playbook_grants")
    op.drop_table("playbook_grants")
    op.drop_column("role_collections", "playbook_policy")
