"""죽은 표 14개 드롭 — `users` 계열 + 스펙 §12 파이프라인 표 (T269 #4 · 사용자 결정 2026-09-11).

Note:
    2026-08-15 목표 변경으로 "제안 → 승인 → 주문 → 포지션" 파이프라인(`trade_proposals` ·
    `approved_orders` · `orders` · `positions` · `transitions` · `risk_plan_revisions` ·
    `risk_policies`)은 미뤄졌고, 계정은 `users` 가 아니라 `accounts`(0104~)가 됐다. `users` 를
    참조하던 표(`broker_credentials` · `account_balances` · `allocation_ledger` · `backtest_runs` ·
    `notifications` · `portfolio_snapshots`)도 코드에서 읽는 곳이 없다. 로컬 두 DB 모두 열넷이
    0행이었다(실계좌도 배포 전 확인). 지키는 것이 데이터가 아니라 빈 스키마뿐이라 지운다 —
    스키마는 0001 과 스펙 §12 에 있다.

    되돌리기(downgrade)는 없다: 빈 표를 되살릴 이유가 없고, 파이프라인을 다시 꺼낼 때는 그때의
    스펙으로 새 마이그레이션을 쓴다.

    Enum 은 전부 `native_enum=False`(VARCHAR + CHECK)라 따로 지울 타입이 없다.

Revision ID: 0129_drop_dead_tables
Revises: 0128_sessions_invalid_before
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0129_drop_dead_tables"
down_revision: str | None = "0128_sessions_invalid_before"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEAD_TABLES: tuple[str, ...] = (
    # 외래키 순서 — 참조하는 쪽이 먼저
    "orders",
    "transitions",
    "risk_plan_revisions",
    "approved_orders",
    "trade_proposals",
    "positions",
    "risk_policies",
    "broker_credentials",
    "account_balances",
    "allocation_ledger",
    "backtest_runs",
    "notifications",
    "portfolio_snapshots",
    "users",
)


def upgrade() -> None:
    """빈 표를 지운다 — 행이 있으면 멈춘다 (조용히 데이터를 버리지 않는다 · 절대 규칙 #8)."""
    bind = op.get_bind()
    for name in DEAD_TABLES:
        exists = bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (name,)
        ).scalar()
        if not exists:
            continue
        rows = bind.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar()
        if rows:
            raise RuntimeError(
                f"{name} 에 {rows}행이 있다 — 죽은 표가 아니다. 사람이 보고 결정한다(0129 중단)"
            )
        op.drop_table(name)


def downgrade() -> None:
    """되돌리지 않는다.

    Raises:
        RuntimeError: 항상 — 빈 표를 되살릴 이유가 없다(모듈 docstring).
    """
    raise RuntimeError("0129 는 되돌리지 않는다 — 빈 표였고 스키마는 0001·스펙 §12 에 있다")
