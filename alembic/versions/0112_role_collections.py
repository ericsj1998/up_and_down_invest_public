"""권한 묶음 표 + 계정의 묶음·개별 권한 (사용자 2026-09-07 "기능별 권한 · 권한 컬렉션").

Note:
    `role_collections` 에 내장 여섯 묶음을 심고, 계정마다 옛 등급에서 묶음을 백필한다
    (admin → super_admin · trader → trader · viewer → viewer · guest → guest · pending → 없음).
    옛 `audit`·`demo_trade` 플래그는 `extra_caps` 로 옮긴다. 옛 열은 **지우지 않는다** — 블루그린 교체
    중 옛 슬롯(1.1.0)이 아직 그 열을 읽는다. 다음 릴리스에서 뺀다.

Revision ID: 0112_role_collections
Revises: 0111_account_demo_trade
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0112_role_collections"
down_revision: str | None = "0111_account_demo_trade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ⚠️ 마이그레이션은 코드의 상수를 import 하지 않는다 — 나중에 상수가 바뀌어도 이 시점의 값이 고정돼야 한다.
_DEMO_READ = "demo_account_read,demo_runs_read"
_LIVE_READ = "live_account_read,live_runs_read"
_BUILTINS = (
    ("guest", "게스트", f"demo_trade,report,{_DEMO_READ}"),
    ("live_watch_guest", "실거래 조회 게스트", f"demo_trade,report,{_DEMO_READ},{_LIVE_READ}"),
    ("viewer", "열람자", f"report,{_DEMO_READ}"),
    ("trader", "거래자", f"demo_trade,live_trade,report,{_DEMO_READ},{_LIVE_READ}"),
    (
        "admin",
        "관리자",
        f"demo_trade,live_trade,audit,report,{_DEMO_READ},{_LIVE_READ},manage_users",
    ),
    (
        "super_admin",
        "슈퍼 관리자",
        f"demo_trade,live_trade,audit,report,{_DEMO_READ},{_LIVE_READ},manage_users,manage_roles",
    ),
)


def upgrade() -> None:
    """묶음 표 · 계정 열 둘 · 내장 묶음 · 백필."""
    op.create_table(
        "role_collections",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("caps", sa.String(), nullable=False, server_default=""),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "accounts", sa.Column("role_collection", sa.String(), nullable=False, server_default="")
    )
    op.add_column(
        "accounts", sa.Column("extra_caps", sa.String(), nullable=False, server_default="")
    )
    table = sa.table(
        "role_collections",
        sa.column("name", sa.String),
        sa.column("label", sa.String),
        sa.column("caps", sa.String),
        sa.column("builtin", sa.Boolean),
        sa.column("updated_by", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {"name": name, "label": label, "caps": caps, "builtin": True, "updated_by": "0112"}
            for name, label, caps in _BUILTINS
        ],
    )
    op.execute(
        sa.text(
            """
            UPDATE accounts SET role_collection = CASE role
                WHEN 'admin' THEN 'super_admin'
                WHEN 'trader' THEN 'trader'
                WHEN 'viewer' THEN 'viewer'
                WHEN 'guest' THEN 'guest'
                ELSE '' END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE accounts SET extra_caps = trim(both ',' from
                (CASE WHEN audit THEN 'audit' ELSE '' END) || ',' ||
                (CASE WHEN demo_trade THEN 'demo_trade' ELSE '' END))
            """
        )
    )


def downgrade() -> None:
    """되돌린다 — 옛 플래그 열은 그대로라 등급 기반으로 다시 돈다."""
    op.drop_column("accounts", "extra_caps")
    op.drop_column("accounts", "role_collection")
    op.drop_table("role_collections")
