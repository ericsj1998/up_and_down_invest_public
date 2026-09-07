"""모의 라이브 교정 원장 — 백테스트 가정을 실측으로 바꾸기 위한 표 (T185).

Note:
    🔴 **추가 전용이다.** `wf_orders` 는 `(run_id, trade_id, role)` 유일키로 역할당
    한 행을 덮어써서 *지금 상태*를 든다. 교정은 *"몇 번 시도했고 그때마다 얼마나
    어긋났나"* 를 세는 일이라 **모든 시도**가 남아야 한다 — 그래서 유일키를 안 건다.

    ⚠️ 이 표가 비어 있어도 매매는 정상이다. 관측 전용이며, 여기 쓰기가 실패해도
    주문 경로가 멈추지 않는다 (절대 규칙 #8-1 — 관측은 리스크 증가 행동이 아니다).

Revision ID: 0107_calibration
Revises: 0106_event_actor
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0107_calibration"
down_revision: str | None = "0106_event_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """교정 원장을 만든다."""
    op.create_table(
        "wf_calibration",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trade_id", sa.String(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default=""),
        sa.Column("intended_price", sa.Numeric(), nullable=True),
        sa.Column("judge_close", sa.Numeric(), nullable=True),
        # 🔴 timestamptz — naive 를 넣으면 도메인·CLI 가드가 거부한다 (절대 규칙 #7).
        sa.Column("judge_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rvol", sa.Numeric(), nullable=True),
        sa.Column("wanted_contracts", sa.Numeric(), nullable=True),
        sa.Column("sent_contracts", sa.Numeric(), nullable=True),
        sa.Column("amount", sa.Numeric(), nullable=True),
        sa.Column("extra_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["run_id"], ["wf_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wf_calibration_run_id", "wf_calibration", ["run_id"])
    op.create_index("ix_wf_calibration_trade_id", "wf_calibration", ["trade_id"])
    op.create_index("ix_wf_calibration_kind", "wf_calibration", ["kind"])


def downgrade() -> None:
    """되돌린다 — ⚠️ 모아 둔 교정 표본이 통째로 사라진다.

    3.7개월을 모아야 100회 전환이다. 지우기 전에 덤프를 뜬다.
    """
    op.drop_index("ix_wf_calibration_kind", table_name="wf_calibration")
    op.drop_index("ix_wf_calibration_trade_id", table_name="wf_calibration")
    op.drop_index("ix_wf_calibration_run_id", table_name="wf_calibration")
    op.drop_table("wf_calibration")
