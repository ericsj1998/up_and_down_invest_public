"""권한 묶음에 수정·삭제 기능 넷을 더한다 (사용자 2026-09-08).

Note:
    새 Cap: `demo_edit` · `demo_delete` · `live_edit` · `live_delete`. 내장 묶음의 `caps` 는
    표(`role_collections`)가 단일 출처라 코드 상수를 바꿔도 표는 그대로다 — 여기서 표를 따라
    올린다. 거래자·관리자·슈퍼 관리자는 넷 다, 게스트·실거래 조회 게스트는 `demo_edit` 만
    (남의 데모 판을 지우는 것은 관리자 몫). 관리자가 이미 고친 묶음이면 그 값에 **더한다**.

Revision ID: 0113_edit_delete_caps
Revises: 0112_role_collections
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0113_edit_delete_caps"
down_revision: str | None = "0112_role_collections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADD = {
    "trader": ("demo_edit", "demo_delete", "live_edit", "live_delete"),
    "admin": ("demo_edit", "demo_delete", "live_edit", "live_delete"),
    "super_admin": ("demo_edit", "demo_delete", "live_edit", "live_delete"),
    "guest": ("demo_edit",),
    "live_watch_guest": ("demo_edit",),
}


def upgrade() -> None:
    """내장 묶음 행에 새 기능을 더한다 (이미 있으면 그대로)."""
    conn = op.get_bind()
    for name, extra in _ADD.items():
        row = conn.execute(
            sa.text("SELECT caps FROM role_collections WHERE name = :name"), {"name": name}
        ).first()
        if row is None:
            continue
        have = [c for c in str(row[0]).split(",") if c]
        for cap in extra:
            if cap not in have:
                have.append(cap)
        conn.execute(
            sa.text(
                "UPDATE role_collections SET caps = :caps, updated_by = '0113' WHERE name = :name"
            ),
            {"caps": ",".join(have), "name": name},
        )


def downgrade() -> None:
    """새 기능을 뺀다 (개별 `extra_caps` 는 코드가 모르는 이름을 무시하므로 그대로 둔다)."""
    conn = op.get_bind()
    gone = {"demo_edit", "demo_delete", "live_edit", "live_delete"}
    rows = conn.execute(sa.text("SELECT name, caps FROM role_collections")).all()
    for name, caps in rows:
        kept = [c for c in str(caps).split(",") if c and c not in gone]
        conn.execute(
            sa.text("UPDATE role_collections SET caps = :caps WHERE name = :name"),
            {"caps": ",".join(kept), "name": name},
        )
