"""candles 월 파티션 관리 (spec §2.1, plan D-4, P-8).

**DDL 문자열을 만들기만 하고 실행하지 않는다.** 실행 주체가 둘이기 때문이다 —
마이그레이션(sync, `op.execute`)과 engine 스케줄러 배치(async, `AsyncConnection`).
순수 함수로 두면 양쪽이 같은 규칙을 쓰고, 테스트도 DB 없이 돌아간다 (원칙 P1).

**미래 파티션이 없으면 INSERT 가 실패한다.** 그래서 버퍼를 2개월 둔다 — 배치가 한 번
실패해도 캔들 적재가 끊기지 않게 (plan D-4).

DEFAULT 파티션은 **일부러 만들지 않는다.** 범위 밖 데이터를 조용히 흡수하면 파티션
누락을 눈치채지 못한다. 실패해서 알림이 가는 편이 낫다 (spec §7 조용한 실패 금지).
"""

from datetime import date, datetime

MONTHS_FORWARD_BUFFER = 2
"""항상 보장할 미래 파티션 개월 수 (plan D-4).

1개월이면 배치가 한 번만 실패해도 다음 달 INSERT 가 깨진다. 2개월이면 한 번의
실패를 흡수한다.
"""


def month_floor(moment: datetime | date) -> date:
    """해당 월의 1일을 돌려준다.

    Args:
        moment: 기준 시각 또는 날짜.

    Returns:
        같은 달의 1일.
    """
    return date(moment.year, moment.month, 1)


def next_month(month_start: date) -> date:
    """다음 달 1일을 돌려준다.

    Args:
        month_start: 어떤 달의 1일.

    Returns:
        다음 달 1일.
    """
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def months_in_window(
    now: datetime | date,
    *,
    months_back: int,
    months_forward: int = MONTHS_FORWARD_BUFFER,
) -> list[date]:
    """보장해야 할 파티션 월 목록을 만든다.

    Args:
        now: 기준 시각. **인자로 받는다** — 현재시각 직접 참조는 결정론 코어 금지
            사항이고(원칙 P1), 테스트가 임의 시점을 재현할 수 있어야 한다.
        months_back: 과거로 몇 개월. 백필 기간을 덮어야 한다 (P0-8 은 1년치).
        months_forward: 미래로 몇 개월. 기본 2개월 버퍼 (plan D-4).

    Returns:
        각 달의 1일 목록, 오름차순.

    Raises:
        ValueError: `months_back` 또는 `months_forward` 가 음수인 경우.
    """
    if months_back < 0 or months_forward < 0:
        raise ValueError(f"개월 수는 음수일 수 없다: back={months_back}, forward={months_forward}")

    start = month_floor(now)
    for _ in range(months_back):
        start = _prev_month(start)

    months: list[date] = []
    cursor = start
    for _ in range(months_back + months_forward + 1):
        months.append(cursor)
        cursor = next_month(cursor)
    return months


def _prev_month(month_start: date) -> date:
    if month_start.month == 1:
        return date(month_start.year - 1, 12, 1)
    return date(month_start.year, month_start.month - 1, 1)


def partition_name(table: str, month_start: date) -> str:
    """파티션 테이블 이름을 만든다.

    Args:
        table: 부모 테이블 이름.
        month_start: 해당 달의 1일.

    Returns:
        `candles_2026_08` 형식의 이름.
    """
    return f"{table}_{month_start.year:04d}_{month_start.month:02d}"


def create_partition_ddl(table: str, month_start: date) -> str:
    """단일 월 파티션 생성 DDL 을 만든다.

    Args:
        table: 부모 테이블 이름.
        month_start: 해당 달의 1일.

    Returns:
        `CREATE TABLE IF NOT EXISTS ... PARTITION OF ...` 문.

    Note:
        `IF NOT EXISTS` 라서 **재실행이 안전하다.** 월 배치가 중복 실행되거나
        마이그레이션이 재적용돼도 문제가 없어야 한다.

        경계는 `[이번 달 1일, 다음 달 1일)` 반개구간이다. PostgreSQL RANGE 파티션의
        FROM 은 포함, TO 는 제외이므로 월말 자정 봉이 어느 쪽에도 안 들어가는
        구멍이 생기지 않는다.
    """
    upper = next_month(month_start)
    return (
        f"CREATE TABLE IF NOT EXISTS {partition_name(table, month_start)} "
        f"PARTITION OF {table} "
        f"FOR VALUES FROM ('{month_start.isoformat()}') TO ('{upper.isoformat()}')"
    )


def ensure_partitions_ddl(
    table: str,
    now: datetime | date,
    *,
    months_back: int,
    months_forward: int = MONTHS_FORWARD_BUFFER,
) -> list[str]:
    """윈도우 전체의 파티션 생성 DDL 목록을 만든다.

    Args:
        table: 부모 테이블 이름.
        now: 기준 시각.
        months_back: 과거로 몇 개월.
        months_forward: 미래로 몇 개월.

    Returns:
        실행할 DDL 문 목록, 과거→미래 순서.

    Note:
        호출부(마이그레이션 / engine 월배치)가 실행한다. **파티션 생성 실패는
        알림 대상**이다 — 파티션 부재는 캔들 적재 전면 중단이라 심각도가 높다
        (spec §7, plan D-4).
    """
    months = months_in_window(now, months_back=months_back, months_forward=months_forward)
    return [create_partition_ddl(table, month) for month in months]
