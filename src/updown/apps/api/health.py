"""의존성 상태 점검 (P0-9-1 · spec §12.6, §4.18).

## liveness 와 readiness 를 나눈다

| 엔드포인트 | 질문 | DB 가 죽었을 때 |
|---|---|---|
| `/health` | **이 프로세스가 살아 있고 응답하는가** | **200** + `status: degraded` |
| `/health/ready` | **지금 트래픽을 받아도 되는가** | **503** |

`/health` 가 DB 장애에 503 을 주면 컨테이너 healthcheck 가 실패해 **API 를 계속 재시작**한다.
재시작이 DB 를 살리지 못하므로 플래핑만 늘고, 로그가 재시작으로 덮여 원인 파악이 더 어려워진다.
반면 로드밸런서는 트래픽을 빼야 하므로 그 판정은 `/health/ready` 가 준다.

## 로그 적재 상태도 보고한다

데드맨 스위치와 같은 발상이다 (§12.6) — **로그가 죽은 것을 로그로만 알리면 아무도 모른다.**
`LogHealthState`(P0-6)를 여기 노출해 외부 헬스체크가 감지할 수 있게 한다.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.logging.audit import LogHealthState

CHECK_TIMEOUT_SECONDS = 3.0
"""의존성 점검 타임아웃.

헬스체크가 오래 걸리면 그 자체로 장애 신호를 늦춘다. 짧게 끊고 `degraded` 로 보고하는
편이 낫다.
"""


class DependencyStatus(StrEnum):
    """개별 의존성 상태.

    Attributes:
        OK: 정상.
        DEGRADED: 응답하지만 정상이 아니다 (예: 로그 적재 실패 누적).
        DOWN: 응답하지 않는다.
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    """의존성 1건의 점검 결과.

    Attributes:
        name: 의존성 이름.
        status: 상태.
        detail: 사람이 읽을 부가 정보. 정상이면 None.
    """

    name: str
    status: DependencyStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HealthReport:
    """전체 상태 (P0-9-1).

    Attributes:
        checks: 의존성별 결과.
        checked_at: 점검 시각 (UTC).

    Note:
        `status` 를 필드가 아니라 **파생값**으로 둔다 — 개별 결과와 종합 판정이 어긋난
        상태를 만들 수 없게 한다.
    """

    checks: tuple[DependencyCheck, ...] = field(default_factory=tuple)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def status(self) -> DependencyStatus:
        """가장 나쁜 의존성 상태를 종합 상태로 삼는다."""
        if any(check.status is DependencyStatus.DOWN for check in self.checks):
            return DependencyStatus.DOWN
        if any(check.status is DependencyStatus.DEGRADED for check in self.checks):
            return DependencyStatus.DEGRADED
        return DependencyStatus.OK

    @property
    def is_ready(self) -> bool:
        """트래픽을 받아도 되는가 (`/health/ready` 판정)."""
        return self.status is DependencyStatus.OK

    def to_json_dict(self) -> dict[str, object]:
        """응답 본문 형태로 만든다.

        Returns:
            `{status, checked_at, dependencies{이름: {status, detail}}}`.
        """
        return {
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "dependencies": {
                check.name: {"status": check.status.value, "detail": check.detail}
                for check in self.checks
            },
        }


async def check_database(session_factory: async_sessionmaker[AsyncSession]) -> DependencyCheck:
    """DB 연결을 확인한다.

    Args:
        session_factory: 세션 팩토리.

    Returns:
        점검 결과.

    Note:
        `SELECT 1` 이다. 테이블을 읽지 않는 이유: 헬스체크가 스키마에 의존하면
        마이그레이션 중에 healthcheck 가 실패해 컨테이너가 재시작된다.
    """
    try:
        async with session_factory() as session:
            await session.execute(sa.text("SELECT 1"))
    except Exception as exc:
        return DependencyCheck("database", DependencyStatus.DOWN, f"{type(exc).__name__}: {exc}")
    return DependencyCheck("database", DependencyStatus.OK)


async def check_redis(redis: Redis) -> DependencyCheck:
    """Redis 연결을 확인한다.

    Args:
        redis: Redis 클라이언트.

    Returns:
        점검 결과.
    """
    try:
        await redis.ping()  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        return DependencyCheck("redis", DependencyStatus.DOWN, f"{type(exc).__name__}: {exc}")
    return DependencyCheck("redis", DependencyStatus.OK)


def check_audit_log(health: LogHealthState) -> DependencyCheck:
    """감사 로그 적재 상태를 보고한다 (spec §12.6, P0-6).

    Args:
        health: 로그 적재 건강 상태.

    Returns:
        점검 결과. 적재 실패가 누적되면 `DEGRADED`.

    Note:
        **`DOWN` 이 아니라 `DEGRADED`** 다. 로그 적재가 실패해도 리스크 감소 행동은 계속
        집행되고 폴백 파일에 기록되므로(§1.2.1), 시스템이 죽은 것은 아니다. 다만 감사
        추적이 DB + 폴백 파일로 쪼개진 상태이므로 조용히 넘길 수도 없다.
    """
    if not health.degraded:
        return DependencyCheck("audit_log", DependencyStatus.OK)
    return DependencyCheck(
        "audit_log",
        DependencyStatus.DEGRADED,
        f"연속 실패 {health.consecutive_failures}회 · "
        f"최근 실패 {health.last_failure_at.isoformat() if health.last_failure_at else '-'} · "
        f"사유={health.last_failure_reason} — "
        "감사 추적이 DB + 폴백 파일의 합집합이다 (복구 배치 필요)",
    )
