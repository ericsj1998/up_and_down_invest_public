"""계정 표 — 구글 로그인과 등급 (2026-08-30 배포 준비).

Note:
    🔴 **등급을 표에 둔다** (세션 쪽지가 아니라). 쪽지에 담으면 관리자가 등급을 내려도
    그 사람의 쪽지가 만료될 때까지 옛 권한이 산다 — 권한 회수가 12시간 뒤에 듣는다.
    표에 두면 다음 요청부터 바로 듣는다.

    🔴 **기본 등급은 `pending`** 이다. 새로 들어온 사람이 아무 권한도 안 갖게.

    ⚠️ **비밀번호 칸이 없다.** 구글이 사람을 확인하고 우리는 결과만 받는다 —
    저장하지 않은 것은 샐 수 없다.

Revision ID: 0105_accounts
Revises: 0104_vault_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0105_accounts"
down_revision: str | None = "0104_vault_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("pending", "viewer", "trader", "admin")
"""등급 — `common/security/roles.Role` 과 **같은 값이어야 한다**.

⚠️ 여기 없는 값이 표에 들어가면 CHECK 가 막는다. 등급을 추가할 때는 마이그레이션이
같이 와야 한다 — 코드만 고치면 새 등급을 못 저장하고, 그 실패가 로그인 경로에서 난다.
"""


def upgrade() -> None:
    """계정 표를 만든다."""
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # 🔴 이메일이 열쇠다 — 관리자가 승인 화면에서 보는 것도, 설정의 첫 관리자도 이것이다.
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("picture", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "role",
            sa.Enum(*ROLES, name="account_role", native_enum=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # ⚠️ **유일 색인은 필수다.** 같은 이메일이 두 행이면 하나만 승인된 상태가 생기고,
    #    어느 행을 읽느냐에 따라 권한이 달라진다.
    op.create_index("ux_accounts_email", "accounts", ["email"], unique=True)


def downgrade() -> None:
    """계정 표를 지운다 — ⚠️ 승인 이력이 통째로 사라진다."""
    op.drop_index("ux_accounts_email", table_name="accounts")
    op.drop_table("accounts")
