"""마이그레이션 검증 (P0-4-8) — 실제 PostgreSQL 이 필요하다.

**전용 테스트 DB 를 쓴다.** 왕복 테스트가 `downgrade base` 로 전 테이블을 지우는데,
dev DB 를 쓰면 P0-8 이 적재한 1년치 백필이 날아간다.

테스트 DB 준비(`migrated_engine` · `test_database_url`)는 `conftest.py` 에 있다 —
`db` 마크가 붙은 다른 모듈도 같은 DB 를 쓰므로, 준비를 이 파일이 갖고 있으면 그 모듈들이
이 파일의 **부수효과에 얹혀 가게** 된다.

`DATABASE_URL` 이 없으면 통째로 skip 한다 — 컨테이너 없이 `pytest` 를 돌리는 경우를
막지 않기 위해서다.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, ProgrammingError

from updown.common.db.models import EXPECTED_TABLE_COUNT
from updown.common.db.partitions import (
    create_partition_ddl,
    month_floor,
    next_month,
    partition_name,
)

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parent.parent


def _base_tables(conn: sa.Connection) -> set[str]:
    rows = conn.execute(
        sa.text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
              AND table_name <> 'alembic_version'
              AND table_name NOT LIKE 'candles\\_%'
        """)
    ).scalars()
    return set(rows)


def test_upgrade_head_creates_all_spec_9_tables(migrated_engine: Engine) -> None:
    """DoD 1·2 — 빈 DB 에서 upgrade 성공 + §9 20개 테이블 전부."""
    with migrated_engine.connect() as conn:
        tables = _base_tables(conn)
    assert len(tables) == EXPECTED_TABLE_COUNT, sorted(tables)
    assert {"risk_plan_revisions", "candle_quality_issues", "orders"} <= tables


def test_candles_is_range_partitioned_by_ts(migrated_engine: Engine) -> None:
    """DoD 3 — `Partition key: RANGE (ts)` + 초기 파티션 존재."""
    with migrated_engine.connect() as conn:
        partkey = conn.execute(
            sa.text("SELECT pg_get_partkeydef('candles'::regclass)")
        ).scalar_one()
        partition_count = conn.execute(
            sa.text("SELECT count(*) FROM pg_inherits WHERE inhparent = 'candles'::regclass")
        ).scalar_one()
    assert partkey == "RANGE (ts)"
    assert partition_count > 0


def test_candles_primary_key_includes_partition_key(migrated_engine: Engine) -> None:
    """plan P-8 — 파티션 테이블의 PK 에는 파티션 키가 반드시 포함된다."""
    with migrated_engine.connect() as conn:
        pk = conn.execute(
            sa.text("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'candles'::regclass AND contype = 'p'
            """)
        ).scalar_one()
    assert "ts" in pk
    assert "instrument_id" in pk
    assert "timeframe" in pk


def test_all_timestamp_columns_are_timezone_aware(migrated_engine: Engine) -> None:
    """DoD 6 — 저장은 전부 UTC (spec §12.3, 절대 규칙 #7)."""
    with migrated_engine.connect() as conn:
        naive = conn.execute(
            sa.text("""
                SELECT table_name || '.' || column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type = 'timestamp without time zone'
            """)
        ).scalars()
    assert list(naive) == []


def test_orders_idempotency_key_is_unique(migrated_engine: Engine) -> None:
    """spec §4.10 · 절대 규칙 #6 — 중복 주문의 마지막 방어선."""
    with migrated_engine.connect() as conn:
        constraints = conn.execute(
            sa.text("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'orders'::regclass AND contype = 'u'
            """)
        ).scalars()
    assert any("idempotency_key" in c for c in constraints)


@pytest.mark.parametrize("table", ["event_logs", "risk_plan_revisions"])
def test_append_only_tables_reject_update_and_delete(migrated_engine: Engine, table: str) -> None:
    """spec §1.2.1 · §6.9 — 애플리케이션 롤은 감사 증거를 고칠 수 없다.

    `risk_plan_revisions` 는 스탑 하향 금지(절대 규칙 #3)의 증거다. 조작 가능하면
    "상향만 했다"는 증명이 무의미해진다.
    """
    with migrated_engine.connect() as conn:
        granted = set(
            conn.execute(
                sa.text("""
                    SELECT privilege_type FROM information_schema.table_privileges
                    WHERE grantee = 'updown_app' AND table_name = :t
                """),
                {"t": table},
            ).scalars()
        )
    assert granted == {"SELECT", "INSERT"}, f"{table} 권한이 append-only 가 아니다: {granted}"


@pytest.mark.parametrize("statement", ["UPDATE {t} SET module = 'x'", "DELETE FROM {t}"])
def test_append_only_enforced_at_runtime(migrated_engine: Engine, statement: str) -> None:
    """권한 표만이 아니라 **실제 실행이 막히는지** 확인한다 (절대 규칙 #8-2)."""
    with migrated_engine.connect() as conn:
        conn.execute(sa.text("SET ROLE updown_app"))
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(sa.text(statement.format(t="event_logs")))
        conn.rollback()


def test_insert_fails_without_partition_then_succeeds_after_creating_it(
    migrated_engine: Engine,
) -> None:
    """DoD 4 — 미래 파티션 부재 시 INSERT 실패 → 자동 생성 후 성공 (plan D-4).

    이것이 파티션 자동 생성이 선택이 아니라 필수인 이유다. DEFAULT 파티션을 두지
    않는 것도 의도다 — 조용히 흡수하면 누락을 눈치채지 못한다 (spec §7).
    """
    far_future = month_floor(datetime.now(UTC) + timedelta(days=365 * 3))

    with migrated_engine.begin() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO instruments (market, symbol, name, asset_type, currency)
                VALUES ('UPBIT', 'KRW-TEST', '테스트', 'coin', 'KRW')
                ON CONFLICT (market, symbol) DO NOTHING
            """)
        )
        instrument_id = conn.execute(
            sa.text("SELECT id FROM instruments WHERE market='UPBIT' AND symbol='KRW-TEST'")
        ).scalar_one()

    insert = sa.text("""
        INSERT INTO candles (instrument_id, timeframe, ts, open, high, low, close, volume)
        VALUES (:iid, '5m', :ts, 1, 1, 1, 1, 1)
    """)
    params = {"iid": instrument_id, "ts": far_future}

    # 파티션이 없으면 실패해야 한다.
    with migrated_engine.connect() as conn, pytest.raises(DatabaseError):
        conn.execute(insert, params)

    # 파티션을 만들면 성공해야 한다.
    with migrated_engine.begin() as conn:
        conn.execute(sa.text(create_partition_ddl("candles", far_future)))
    with migrated_engine.begin() as conn:
        conn.execute(insert, params)
        landed = conn.execute(
            sa.text("SELECT count(*) FROM candles WHERE ts = :ts"), {"ts": far_future}
        ).scalar_one()
    assert landed == 1

    # 정리 — 다음 테스트에 영향을 주지 않게.
    with migrated_engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {partition_name('candles', far_future)}"))
        conn.execute(sa.text("DELETE FROM instruments WHERE symbol = 'KRW-TEST'"))


def test_partition_boundary_routes_to_correct_month(migrated_engine: Engine) -> None:
    """월 경계 전후의 ts 가 각각 올바른 파티션에 들어간다."""
    this_month = month_floor(datetime.now(UTC))
    following = next_month(this_month)

    with migrated_engine.begin() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO instruments (market, symbol, name, asset_type, currency)
                VALUES ('UPBIT', 'KRW-BOUND', '경계', 'coin', 'KRW')
                ON CONFLICT (market, symbol) DO NOTHING
            """)
        )
        iid = conn.execute(
            sa.text("SELECT id FROM instruments WHERE market='UPBIT' AND symbol='KRW-BOUND'")
        ).scalar_one()
        for ts in (this_month, following):
            conn.execute(
                sa.text("""
                    INSERT INTO candles
                        (instrument_id, timeframe, ts, open, high, low, close, volume)
                    VALUES (:iid, '1d', :ts, 1, 1, 1, 1, 1)
                """),
                {"iid": iid, "ts": ts},
            )

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT ts::date::text, tableoid::regclass::text
                FROM candles WHERE instrument_id = :iid ORDER BY ts
            """),
            {"iid": iid},
        ).all()
    placements = {str(row[0]): str(row[1]) for row in rows}

    assert placements[this_month.isoformat()] == partition_name("candles", this_month)
    assert placements[following.isoformat()] == partition_name("candles", following)

    with migrated_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM candles WHERE instrument_id = :iid"), {"iid": iid})
        conn.execute(sa.text("DELETE FROM instruments WHERE symbol = 'KRW-BOUND'"))


def test_downgrade_base_then_upgrade_head_round_trip(
    migrated_engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD 5 — 왕복 성공.

    naming_convention 이 없으면 여기서 깨진다. DB 가 자동 명명한 제약을 Alembic 이
    재현하지 못해 downgrade 가 이름을 못 찾기 때문이다.

    Note:
        이 테스트는 전 테이블을 지우므로 **가장 마지막**에 둔다. 끝난 뒤 상태는
        다시 head 다.

        `DATABASE_URL` 은 `monkeypatch` 로 덮어 **테스트가 끝나면 되돌린다.** 그냥
        `os.environ` 에 넣으면 이후 모듈이 테스트 DB 를 dev DB 로 착각한다.
    """
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))

    command.downgrade(cfg, "base")
    with migrated_engine.connect() as conn:
        assert _base_tables(conn) == set()

    command.upgrade(cfg, "head")
    with migrated_engine.connect() as conn:
        assert len(_base_tables(conn)) == EXPECTED_TABLE_COUNT


def test_market_column_fits_every_market_value(migrated_engine: Engine) -> None:
    """T251 — `instruments.market` 폭이 `Market` 의 가장 긴 값 이상이다.

    BINANCE(7자)가 VARCHAR(6) 에 안 들어가던 사고.
    """
    from updown.common.domain.instrument import Market

    with migrated_engine.connect() as conn:
        width = conn.execute(
            sa.text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'instruments' AND column_name = 'market'"
            )
        ).scalar_one()
    longest = max(len(m.value) for m in Market)
    assert width is not None and width >= longest, f"폭 {width} < 가장 긴 시장 이름 {longest}"
