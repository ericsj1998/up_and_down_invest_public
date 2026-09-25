"""불타기 기록 — `wf_trades.add_json` (T308 ⑥ · 2026-09-25).

Note:
    돌파 롱 · MACD 숏 불타기(확인된 강한 움직임에 처음 크기의 0.5 배를 더 싣는다)는
    판정 · 펀드 문의 답 · 거래소 체결 · 추가분 손익을 원 매매에 붙여 적는다.
    재시작 뒤 원장이 추가를 잊으면 거래소 포지션이 원장보다 커 보이고
    추가분 손익이 원장 실현에서 빠진다 — 그래서 저장한다.
    칸 하나(JSONB)에 담는다. NULL = 옛 행 · 불타기 판정이 없는 매매. 더하기만.

Revision ID: 0132_trade_add_json
Revises: 0131_trade_filled_lev
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0132_trade_add_json"
down_revision: str | None = "0131_trade_filled_lev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """불타기 칸을 더한다 (NULL 허용 — 옛 행은 추가 없음)."""
    op.add_column("wf_trades", sa.Column("add_json", JSONB(), nullable=True))


def downgrade() -> None:
    """칸을 뺀다."""
    op.drop_column("wf_trades", "add_json")
