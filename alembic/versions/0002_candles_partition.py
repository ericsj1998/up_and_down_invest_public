"""candles 월 파티셔닝 (P0-4-6).

**수기 DDL 인 이유**: Alembic autogenerate 는 `PARTITION BY` 를 만들지 못한다
(plan P-8). `env.py` 의 `include_object` 가 이 테이블을 autogenerate 에서 제외하고,
여기서 직접 만든다.

**PK 에 파티션 키(`ts`)가 포함**되어야 한다 — PostgreSQL 의 제약이다.
그래서 `(instrument_id, timeframe, ts)` 다.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op

from updown.common.db.partitions import ensure_partitions_ddl

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "candles"

# P0-8 백필이 1년치(5m/1h/1d)를 넣는다. 13개월을 덮어 월 경계 여유를 둔다.
# 미래분은 D-4 의 2개월 버퍼가 기본값으로 붙는다.
MONTHS_BACK = 13


def upgrade() -> None:
    """파티션 부모 + 초기 파티션을 만든다."""
    op.execute(f"""
        CREATE TABLE {TABLE} (
            instrument_id BIGINT NOT NULL REFERENCES instruments(id),
            timeframe     VARCHAR(3) NOT NULL,
            ts            TIMESTAMP WITH TIME ZONE NOT NULL,
            open          NUMERIC(38, 18) NOT NULL,
            high          NUMERIC(38, 18) NOT NULL,
            low           NUMERIC(38, 18) NOT NULL,
            close         NUMERIC(38, 18) NOT NULL,
            volume        NUMERIC(38, 18) NOT NULL,
            CONSTRAINT pk_candles PRIMARY KEY (instrument_id, timeframe, ts),
            CONSTRAINT ck_candles_timeframe
                CHECK (timeframe IN ('5m', '15m', '1h', '4h', '1d'))
        ) PARTITION BY RANGE (ts)
    """)

    # 조회 패턴은 "종목 + TF 의 기간 슬라이스"다 (spec §4.13, §4.11).
    # PK 와 컬럼 순서가 같아 PK 인덱스로 커버되지만, 파티션별 로컬 인덱스가
    # 자동 생성되도록 부모에 명시적으로 걸어 둔다.
    op.execute(f"CREATE INDEX ix_candles_lookup ON {TABLE} (instrument_id, timeframe, ts DESC)")

    # engine 스케줄러가 아직 없는 시점에도 P0-8 백필이 돌아야 한다 → 선생성 (plan D-4).
    for statement in ensure_partitions_ddl(TABLE, datetime.now(UTC), months_back=MONTHS_BACK):
        op.execute(statement)


def downgrade() -> None:
    """부모를 지우면 파티션도 함께 사라진다."""
    op.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
