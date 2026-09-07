"""`/health` 검증 (P0-9-1·6 · spec §12.6, §2.1 / G0-6).

DoD 2 — `/health` 200 + DB/Redis 상태 `ok`
G0-6 — `/health` 호출이 `event_logs` 에 trace_id 와 함께 적재

liveness(`/health`)와 readiness(`/health/ready`)의 **응답 코드 차이**가 이 파일의 핵심
검증 대상이다. DB 가 죽었을 때 liveness 가 503 을 주면 컨테이너가 API 를 계속 재시작한다.
"""

import os
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from updown.apps.api.health import (
    DependencyCheck,
    DependencyStatus,
    HealthReport,
    check_audit_log,
)
from updown.apps.api.main import HTTP_SERVICE_UNAVAILABLE, ApiState, create_app
from updown.apps.api.middleware import TRACE_HEADER
from updown.common.config import AppEnv, Settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.logging.audit import LogHealthState

# ---------------------------------------------------------------------------
# 1. 리포트 조립 — 순수 로직
# ---------------------------------------------------------------------------


def test_status_is_the_worst_dependency() -> None:
    """하나라도 죽으면 종합 상태는 죽음이다 — 평균을 내지 않는다."""
    report = HealthReport(
        checks=(
            DependencyCheck("database", DependencyStatus.OK),
            DependencyCheck("redis", DependencyStatus.DOWN, "connection refused"),
            DependencyCheck("audit_log", DependencyStatus.DEGRADED, "실패 3회"),
        )
    )
    assert report.status is DependencyStatus.DOWN
    assert report.is_ready is False


def test_degraded_is_not_ready() -> None:
    """감사 추적이 쪼개진 상태로 트래픽을 받지 않는다."""
    report = HealthReport(
        checks=(
            DependencyCheck("database", DependencyStatus.OK),
            DependencyCheck("audit_log", DependencyStatus.DEGRADED, "실패 1회"),
        )
    )
    assert report.status is DependencyStatus.DEGRADED
    assert report.is_ready is False


def test_all_ok_is_ready() -> None:
    report = HealthReport(checks=(DependencyCheck("database", DependencyStatus.OK),))
    assert report.status is DependencyStatus.OK
    assert report.is_ready is True


def test_report_serializes_every_dependency() -> None:
    report = HealthReport(
        checks=(
            DependencyCheck("database", DependencyStatus.OK),
            DependencyCheck("redis", DependencyStatus.DOWN, "boom"),
        )
    )
    body = report.to_json_dict()
    dependencies = cast("dict[str, Any]", body["dependencies"])
    assert set(dependencies) == {"database", "redis"}
    assert dependencies["redis"]["detail"] == "boom"
    assert isinstance(body["checked_at"], str)


def test_audit_log_failure_is_degraded_not_down() -> None:
    """로그 적재가 실패해도 손절은 집행된다 (§1.2.1) — 시스템이 죽은 것은 아니다."""
    health = LogHealthState()
    health.record_failure("connection refused")
    check = check_audit_log(health)
    assert check.status is DependencyStatus.DEGRADED
    assert check.detail is not None
    assert "폴백" in check.detail, "감사 추적이 쪼개졌다는 사실이 드러나야 한다"


def test_healthy_audit_log_is_ok() -> None:
    assert check_audit_log(LogHealthState()).status is DependencyStatus.OK


# ---------------------------------------------------------------------------
# 2. 엔드포인트 — 대역 주입
# ---------------------------------------------------------------------------


class _StubState(ApiState):
    """DB·Redis 없이 상태를 흉내내는 대역.

    Note:
        `ApiState.__init__` 을 건너뛴다 — 그 안에서 엔진과 Redis 연결이 만들어지므로,
        컨테이너 없이 엔드포인트 형태만 검증하려면 우회해야 한다.
    """

    def __init__(self, report: HealthReport) -> None:
        #: 테스트가 도중에 갈아끼운다 (상태 변화 검증) — 그래서 공개 속성이다.
        self.report = report
        self.recorded: list[HealthReport] = []
        self.last_reported_status: DependencyStatus | None = None
        self.settings = Settings(
            app_env=AppEnv.DEV,
            database_url="postgresql+psycopg://x:y@localhost/z",
            redis_url="redis://localhost:6379/0",
        )
        # ⭐ 기동 훅이 판 저장소를 붙일 때 이 값을 읽는다 (T16 ②).
        #    엔진은 **게으르다** — 실제 연결은 첫 쿼리에서 열리므로 컨테이너 없이도
        #    만들 수 있다. 이 테스트는 쿼리를 한 번도 안 낸다.
        self.session_factory = create_session_factory(create_engine(self.settings.database_url))

    async def collect_health(self) -> HealthReport:
        return self.report

    async def record_status_change(self, report: HealthReport) -> None:
        if report.status is self.last_reported_status:
            return
        self.last_reported_status = report.status
        self.recorded.append(report)

    async def aclose(self) -> None:
        return None


def make_client(report: HealthReport) -> tuple[TestClient, _StubState]:
    """대역이 물린 테스트 클라이언트."""
    state = _StubState(report)
    return TestClient(create_app(state)), state


OK_REPORT = HealthReport(
    checks=(
        DependencyCheck("database", DependencyStatus.OK),
        DependencyCheck("redis", DependencyStatus.OK),
        DependencyCheck("audit_log", DependencyStatus.OK),
    )
)
DB_DOWN_REPORT = HealthReport(
    checks=(
        DependencyCheck("database", DependencyStatus.DOWN, "connection refused"),
        DependencyCheck("redis", DependencyStatus.OK),
        DependencyCheck("audit_log", DependencyStatus.OK),
    )
)


def test_health_returns_200_and_dependency_detail() -> None:
    """DoD 2 — 200 + DB/Redis 상태."""
    client, _ = make_client(OK_REPORT)
    with client:
        response = cast("httpx.Response", client.get("/health"))  # pyright: ignore[reportUnknownMemberType]
    assert response.status_code == 200
    body = cast("dict[str, Any]", response.json())
    assert body["status"] == "ok"
    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["redis"]["status"] == "ok"


def test_health_stays_200_when_the_database_is_down() -> None:
    """**liveness 는 의존성 장애에 503 을 주지 않는다.**

    503 이면 컨테이너 healthcheck 가 실패해 API 를 계속 재시작한다. 재시작이 DB 를 살리지
    못하므로 플래핑만 늘고, 로그가 재시작으로 덮여 원인 파악이 더 어려워진다.
    """
    client, _ = make_client(DB_DOWN_REPORT)
    with client:
        response = cast("httpx.Response", client.get("/health"))  # pyright: ignore[reportUnknownMemberType]
    assert response.status_code == 200
    assert cast("dict[str, Any]", response.json())["status"] == "down"


def test_ready_returns_503_when_the_database_is_down() -> None:
    """readiness 는 트래픽을 뺀다 — 그 판정이 여기 있다."""
    client, _ = make_client(DB_DOWN_REPORT)
    with client:
        response = cast("httpx.Response", client.get("/health/ready"))  # pyright: ignore[reportUnknownMemberType]
    assert response.status_code == HTTP_SERVICE_UNAVAILABLE


def test_ready_returns_200_when_healthy() -> None:
    client, _ = make_client(OK_REPORT)
    with client:
        response = cast("httpx.Response", client.get("/health/ready"))  # pyright: ignore[reportUnknownMemberType]
    assert response.status_code == 200


def test_health_echoes_the_trace_id() -> None:
    """G0-6 의 전제 — 응답에 trace_id 가 실려 로그와 이어진다."""
    client, _ = make_client(OK_REPORT)
    with client:
        response = cast(
            "httpx.Response",
            client.get("/health", headers={TRACE_HEADER: "abcdef1234"}),  # pyright: ignore[reportUnknownMemberType]
        )
    assert response.headers[TRACE_HEADER] == "abcdef1234"


def test_first_call_records_an_audit_event() -> None:
    """G0-6 — 기동 후 첫 호출은 감사 로그를 남긴다."""
    client, state = make_client(OK_REPORT)
    with client:
        client.get("/health")  # pyright: ignore[reportUnknownMemberType]
    assert len(state.recorded) == 1


def test_repeated_calls_do_not_flood_the_audit_log() -> None:
    """healthcheck 가 10초마다 부른다 — 매 호출 적재는 하루 8천 행이다.

    상태 변화만 남기면 "언제부터 죽었나"라는 감사 로그의 질문에 그대로 답하면서 노이즈가 없다.
    """
    client, state = make_client(OK_REPORT)
    with client:
        for _ in range(5):
            client.get("/health")  # pyright: ignore[reportUnknownMemberType]
    assert len(state.recorded) == 1, "같은 상태를 반복 적재했다"


def test_status_change_records_a_new_event() -> None:
    """상태가 바뀌면 남긴다 — 그게 감사 로그가 답해야 하는 것이다."""
    state = _StubState(OK_REPORT)
    client = TestClient(create_app(state))
    with client:
        client.get("/health")  # pyright: ignore[reportUnknownMemberType]
        state.report = DB_DOWN_REPORT
        client.get("/health")  # pyright: ignore[reportUnknownMemberType]
    assert [report.status for report in state.recorded] == [
        DependencyStatus.OK,
        DependencyStatus.DOWN,
    ]


# ---------------------------------------------------------------------------
# 3. 실제 의존성 (db 마커)
# ---------------------------------------------------------------------------


@pytest.fixture
def real_settings(migrated_test_database: str) -> Iterator[Settings]:
    """실 DB·Redis 를 가리키는 설정."""
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL 이 없다 — `make up` 후 실행된다")
    yield Settings(
        app_env=AppEnv.DEV,
        database_url=migrated_test_database,
        redis_url=redis_url,
    )


@pytest.mark.db
async def test_real_dependencies_report_ok(real_settings: Settings) -> None:
    """DoD 2 — 실제 DB·Redis 로 `ok` 가 나온다."""
    state = ApiState(real_settings)
    try:
        report = await state.collect_health()
    finally:
        await state.aclose()

    assert report.status is DependencyStatus.OK, report.to_json_dict()
    assert report.is_ready is True


@pytest.mark.db
async def test_unreachable_database_reports_down(real_settings: Settings) -> None:
    """장애를 `down` 으로 보고해야 한다 — 조용히 ok 를 주면 헬스체크가 무의미하다."""
    broken = Settings(
        app_env=AppEnv.DEV,
        # 닿을 수 없는 포트. 컨테이너를 실제로 내리면 병렬 테스트를 깨뜨린다.
        database_url="postgresql+psycopg://x:y@127.0.0.1:1/nope",
        redis_url=real_settings.redis_url,
    )
    state = ApiState(broken)
    try:
        report = await state.collect_health()
    finally:
        await state.aclose()

    statuses = {check.name: check.status for check in report.checks}
    assert statuses["database"] is DependencyStatus.DOWN
    assert statuses["redis"] is DependencyStatus.OK
    assert report.status is DependencyStatus.DOWN


@pytest.mark.db
async def test_health_call_lands_in_event_logs_with_the_trace_id(
    real_settings: Settings,
) -> None:
    """**G0-6** — `/health` 호출이 `event_logs` 에 trace_id 와 함께 적재된다."""
    from sqlalchemy.ext.asyncio import create_async_engine

    state = ApiState(real_settings)
    client = TestClient(create_app(state))
    trace_id = "0f1e2d3c4b5a6978"
    try:
        with client:
            response = cast(
                "httpx.Response",
                client.get("/health", headers={TRACE_HEADER: trace_id}),  # pyright: ignore[reportUnknownMemberType]
            )
        assert response.status_code == 200
    finally:
        pass  # 자원 정리는 lifespan 이 한다

    engine = create_async_engine(real_settings.database_url)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.text("""
                SELECT event_type, module FROM event_logs
                WHERE trace_id = :trace_id ORDER BY ts
            """),
                {"trace_id": trace_id},
            )
        ).all()
    await engine.dispose()

    assert rows, "event_logs 에 /health 호출 흔적이 없다 (G0-6 미충족)"
    assert rows[0].module == "apps.api.health"
