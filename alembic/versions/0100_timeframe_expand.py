"""캔들 시간축 10종으로 확장 (보기 전용 하위 축 추가).

🔴 **DB 체크 제약이 도메인 열거형보다 좁으면 조용히 막힌다.** `Timeframe` 에 값을 더해도
`ck_candles_timeframe` 이 옛 목록이면, 그 축을 저장하려는 순간 제약 위반이 난다 —
그리고 그 실패는 적재 경로 깊은 곳에서 나서 원인이 안 보인다.

⭐ 하위 축(10s·30s·1m)과 30m·8h 는 **보기 전용**이다 (사용자 확정 2026-08-18). 적재 대상은
`config/backfill.yml` 이 정하고 거기에는 넣지 않았다 — 그래도 제약은 넓혀 둔다. 화면에서
띄운 봉을 캐시하거나 나중에 적재하기로 정할 때, 제약이 좁으면 그때 다시 막힌다.

⚠️ 파티션 테이블이라 제약을 **부모에 다시 건다** (자식은 부모를 따른다).

Revision ID: 0100_timeframe_expand
Revises: 0003
"""

from alembic import op

revision: str = "0100_timeframe_expand"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None

ALLOWED = ("10s", "30s", "1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d")
OLD_ALLOWED = ("5m", "15m", "1h", "4h", "1d")


def _values(items: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in items)


def upgrade() -> None:
    """제약을 10종으로 넓힌다."""
    op.execute("ALTER TABLE candles DROP CONSTRAINT IF EXISTS ck_candles_timeframe")
    op.execute(
        "ALTER TABLE candles ADD CONSTRAINT ck_candles_timeframe "
        f"CHECK (timeframe IN ({_values(ALLOWED)}))"
    )


def downgrade() -> None:
    """되돌린다.

    ⚠️ **새 축의 행이 있으면 실패한다.** 그것이 맞다 — 조용히 지우면 데이터가 사라진다.
    """
    op.execute("ALTER TABLE candles DROP CONSTRAINT IF EXISTS ck_candles_timeframe")
    op.execute(
        "ALTER TABLE candles ADD CONSTRAINT ck_candles_timeframe "
        f"CHECK (timeframe IN ({_values(OLD_ALLOWED)}))"
    )
