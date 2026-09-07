"""월 파티션 DDL 생성 규칙 (P0-4-7) — DB 없이 도는 순수 단위 테스트.

파티션 계산을 순수 함수로 뺀 이유가 이것이다. 마이그레이션과 engine 월배치가 같은
규칙을 쓰는지 확인하는 데 DB 가 필요하지 않다.
"""

from datetime import UTC, date, datetime

import pytest

from updown.common.db.partitions import (
    MONTHS_FORWARD_BUFFER,
    create_partition_ddl,
    ensure_partitions_ddl,
    month_floor,
    months_in_window,
    next_month,
    partition_name,
)


def test_month_floor_truncates_to_first_day() -> None:
    assert month_floor(datetime(2026, 8, 17, 23, 59, tzinfo=UTC)) == date(2026, 8, 1)


def test_next_month_crosses_year_boundary() -> None:
    assert next_month(date(2026, 12, 1)) == date(2027, 1, 1)


def test_window_includes_current_month_and_forward_buffer() -> None:
    """plan D-4 — 항상 '현재 + 2개월'까지 보장한다."""
    months = months_in_window(date(2026, 8, 3), months_back=0)
    assert months == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
    assert MONTHS_FORWARD_BUFFER == 2


def test_window_covers_backfill_period() -> None:
    """P0-8 은 1년치를 백필한다 — 13개월 뒤로 잡으면 월 경계 여유까지 덮는다."""
    months = months_in_window(date(2026, 8, 3), months_back=13)
    assert months[0] == date(2025, 7, 1)
    assert months[-1] == date(2026, 10, 1)
    assert len(months) == 13 + 1 + MONTHS_FORWARD_BUFFER


def test_window_rejects_negative_months() -> None:
    with pytest.raises(ValueError, match="음수"):
        months_in_window(date(2026, 8, 3), months_back=-1)


def test_partition_name_is_zero_padded() -> None:
    assert partition_name("candles", date(2026, 8, 1)) == "candles_2026_08"


def test_partition_bounds_are_half_open() -> None:
    """`[이번 달 1일, 다음 달 1일)` — 월말 자정 봉이 빠지는 구멍이 없어야 한다."""
    ddl = create_partition_ddl("candles", date(2026, 12, 1))
    assert "FROM ('2026-12-01') TO ('2027-01-01')" in ddl


def test_partition_ddl_is_reentrant() -> None:
    """월 배치가 중복 실행돼도 안전해야 한다."""
    assert "IF NOT EXISTS" in create_partition_ddl("candles", date(2026, 8, 1))


def test_ensure_partitions_ddl_is_ordered_past_to_future() -> None:
    statements = ensure_partitions_ddl("candles", date(2026, 8, 3), months_back=2)
    names = [s.split("CREATE TABLE IF NOT EXISTS ")[1].split(" ")[0] for s in statements]
    assert names == [
        "candles_2026_06",
        "candles_2026_07",
        "candles_2026_08",
        "candles_2026_09",
        "candles_2026_10",
    ]
