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
    """DoD 1·2 — 빈 DB 에서 upgrade 성공 + 살아 있는 표 전부 (0129 가 죽은 표를 지운 뒤).

    `orders` · `risk_plan_revisions` 등 14표는 1.7.0(0129)에서 **없어진 것이 맞다** —
    있으면 마이그레이션이 되돌아간 것이다.
    """
    with migrated_engine.connect() as conn:
        tables = _base_tables(conn)
    assert len(tables) == EXPECTED_TABLE_COUNT, sorted(tables)
    assert {"candle_quality_issues", "event_logs", "wf_orders", "accounts"} <= tables
    assert not ({"risk_plan_revisions", "orders", "users"} & tables), sorted(tables)


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


def test_wf_orders_role_key_is_unique(migrated_engine: Engine) -> None:
    """spec §4.10 · 절대 규칙 #6 — 중복 주문의 마지막 방어선.

    실제 주문 원장은 `wf_orders` 다(옛 `orders` 는 0129 에서 지웠다). 한 판·한 매매·한 역할
    (진입/손절/익절)에 행이 둘이면 같은 주문이 두 번 나간 것이다 —
    `uq_wf_orders_run_trade_role` 이 그것을 막는다.
    """
    with migrated_engine.connect() as conn:
        constraints = conn.execute(
            sa.text("""
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'wf_orders'::regclass AND contype = 'u'
            """)
        ).scalars()
    assert "uq_wf_orders_run_trade_role" in set(constraints)


@pytest.mark.parametrize("table", ["event_logs"])
def test_append_only_tables_reject_update_and_delete(migrated_engine: Engine, table: str) -> None:
    """spec §1.2.1 · §6.9 — 애플리케이션 롤은 감사 증거를 고칠 수 없다.

    `risk_plan_revisions`(스탑 하향 금지의 증거 표)는 0129 에서 지웠다 — 그 증거는 지금
    `event_logs` 의 스탑 상향 사건이 진다. 남은 append-only 표는 `event_logs` 하나다.
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


def test_downgrade_past_0129_is_refused_and_schema_stays_head(
    migrated_engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD 5 개정 — 0129(죽은 표 드롭)는 **되돌리지 않는다**: 시도하면 소리 내어 멈추고
    스키마는 head 그대로다.

    옛 왕복(`downgrade base` → `upgrade head`)은 1.7.0 부터 성립하지 않는다 — 빈 표를
    되살릴 이유가 없어 0129 의 downgrade 가 `RuntimeError` 를 낸다(모듈 docstring). 그 결정을
    여기서 고정한다: 조용히 통과하거나 표가 반쯤 지워진 채 남으면 안 된다.

    Note:
        `DATABASE_URL` 은 `monkeypatch` 로 덮어 **테스트가 끝나면 되돌린다.** 그냥
        `os.environ` 에 넣으면 이후 모듈이 테스트 DB 를 dev DB 로 착각한다.
    """
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))

    with pytest.raises(RuntimeError, match="0129"):
        command.downgrade(cfg, "base")
    with migrated_engine.connect() as conn:
        assert len(_base_tables(conn)) == EXPECTED_TABLE_COUNT

    # head 로의 upgrade 는 할 일이 없어야 한다 (이미 head).
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
