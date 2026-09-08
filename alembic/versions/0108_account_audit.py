"""계정 감사 권한 — `accounts.audit` (사용자 2026-09-06).

Note:
    등급(`role`)과 별개 플래그다. 백테스트·합성 미래의 **최종 손익·연차별 손익**은 이 플래그(또는
    관리자)가 있어야 보인다. 차트·지표·매매법·봉·매매 표기는 그대로 본다.

    ⚠️ 하위 호환: 기본 false — 배포 순간 아무도 새 권한을 얻지 않는다. 관리자는 코드에서 늘 참이라
    표를 안 고쳐도 계속 본다.

Revision ID: 0108_account_audit
Revises: 0107_calibration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0108_account_audit"
down_revision: str | None = "0107_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """감사 플래그 열을 더한다 (기본 false)."""
    op.add_column(
        "accounts",
        sa.Column("audit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """감사 플래그 열을 뺀다."""
    op.drop_column("accounts", "audit")
