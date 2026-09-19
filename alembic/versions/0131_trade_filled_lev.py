"""체결이 실제로 만든 노출 — `wf_trades.filled_leverage` (T288 · 2026-09-19).

Note:
    총 명목 상한(`SlotGate` → `open_exposure`)은 기록된 `leverage` 합을 셌는데, 그 값은 **의도한**
    배율이다. 계약은 정수라 실제로 산 것은 그것과 다르고(Gate 실측: SOL 계약 하나 111.55 USDT ·
    DOGE 0.87 — 128배), v1.10.3 반올림 이후로는 **더 살 수도** 있다. 그래서 상한이 두 방향으로
    틀렸다 — 덜 산 만큼 방이 묶여 놀고, 더 산 만큼은 **상한을 넘겨도 안 보였다**(안전 쪽 결함).

    체결 명목 ÷ `sizing_base` 를 여기 적어 상한이 실제를 세게 한다. `leverage` 는 그대로 둔다 —
    손익률이 그것을 쓰고 결정론 코어는 같은 입력에 같은 출력을 내야 한다 (규칙 #5).
    NULL = 모형(백테스트·페이퍼) · 옛 행 · 체결 수량을 못 읽은 경우. 더하기만.

Revision ID: 0131_trade_filled_lev
Revises: 0130_trade_margin_used
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0131_trade_filled_lev"
down_revision: str | None = "0130_trade_margin_used"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """실측 노출 열을 더한다 (NULL 허용 — 옛 행은 의도한 leverage 로 센다)."""
    op.add_column("wf_trades", sa.Column("filled_leverage", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    """열을 뺀다."""
    op.drop_column("wf_trades", "filled_leverage")
