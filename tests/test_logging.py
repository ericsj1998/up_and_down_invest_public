"""로깅 기반 검증 (P0-6-5 · spec §4.14, §12.3, §8).

DoD 2 — stdout 로그 전 라인이 `json.loads` 가능
DoD 3 — 잡 단위 trace_id 존재
DoD 4 — 로그에 시크릿 평문 없음

DoD 1(`/health` → `event_logs` trace_id 일치)은 **실제 DB** 가 필요하므로 아래
`db` 마커 구역에서 확인한다. `/health` 엔드포인트 자체는 P0-9-1 이라, 여기서는
미들웨어에 최소 앱을 붙여 같은 경로(요청 → trace_id → event_logs)를 검증한다.
"""

import asyncio
import io
import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.apps.api.middleware import (
    TRACE_HEADER,
    resolve_incoming_trace_id,
    trace_id_middleware,
)
from updown.apps.engine.log_recovery import (
    LogRecoveryError,
    recover_fallback_logs,
)
from updown.common.db.models.enums import LogLevel
from updown.common.db.session import create_engine, create_session_factory
from updown.common.logging.audit import AuditLogger, LogAttempt
from updown.common.logging.context import (
    get_trace_id,
    new_trace_id,
    trace_context,
)
from updown.common.logging.event_sink import EventRecord, PostgresEventSink
from updown.common.logging.fallback_sink import FallbackSink, FallbackSinkError
from updown.common.logging.policy import ActionRisk
from updown.common.logging.setup import configure_logging, get_logger


class _Silent:
    def notify(self, attempt: LogAttempt) -> None:
        pass


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    yield stream
    configure_logging("INFO")


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    """모든 출력 라인을 JSON 으로 파싱한다 (DoD 2)."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 1. 구조화 로그 형태 (DoD 2)
# ---------------------------------------------------------------------------


def test_every_log_line_is_valid_json(log_stream: io.StringIO) -> None:
    """DoD 2 — 전 라인이 `json.loads` 가능해야 한다."""
    log = get_logger("marketdata.ingest")
    log.info("CANDLE_BACKFILL_DONE", payload={"instrument": "KRW-BTC"})
    log.warning("CANDLE_GAP_DETECTED", missing=3)
    assert len(_lines(log_stream)) == 2


def test_fixed_fields_are_present(log_stream: io.StringIO) -> None:
    """표준 필드 고정 — 이름이 흔들리면 검색·집계가 불가능하다 (§4.14)."""
    get_logger("execution.order_service").info("ORDER_SUBMITTED")
    entry = _lines(log_stream)[0]
    assert {"ts", "level", "module", "event_type", "trace_id", "payload"} <= set(entry)
    assert entry["module"] == "execution.order_service"
    assert entry["event_type"] == "ORDER_SUBMITTED"


def test_timestamp_is_iso8601_utc(log_stream: io.StringIO) -> None:
    """spec §12.3 · 절대 규칙 #7 — 로컬 시각으로 찍으면 로그 순서가 뒤바뀐다."""
    get_logger("m").info("E")
    ts = str(_lines(log_stream)[0]["ts"])
    assert re.match(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)
    assert ts.endswith("Z") or "+00:00" in ts


def test_extra_keys_are_collected_into_payload(log_stream: io.StringIO) -> None:
    """예약 필드가 아닌 키는 `payload` 로 모인다 — 최상위 스키마가 흔들리지 않게."""
    get_logger("m").info("E", symbol="KRW-BTC", qty=3)
    payload = _lines(log_stream)[0]["payload"]
    assert payload == {"symbol": "KRW-BTC", "qty": 3}


def test_trace_id_is_injected_from_context(log_stream: io.StringIO) -> None:
    """DoD 3 — 잡·요청 단위 trace_id 가 로그에 실린다."""
    with trace_context("deadbeef") as trace_id:
        get_logger("apps.engine.scheduler").info("JOB_STARTED")
    assert trace_id == "deadbeef"
    assert _lines(log_stream)[0]["trace_id"] == "deadbeef"


def test_trace_id_is_null_outside_any_context(log_stream: io.StringIO) -> None:
    """없는 것을 없다고 적어야 "요청 밖 로그"임을 알 수 있다 — 가짜 값을 만들지 않는다."""
    get_logger("apps.engine.main").info("BOOT")
    assert _lines(log_stream)[0]["trace_id"] is None


def test_logger_made_before_configure_still_renders_json() -> None:
    """🔴 모듈 로드 때 만든 로거가 나중 설정을 따라야 한다 (2026-09-04 실측 버그).

    `get_logger().bind()` 는 그 순간의 설정에 묶여, `configure_logging` 뒤에도 기본 콘솔
    렌더러로 stdout 에 평문을 찍었다 — `live_run_resumed`·`ratelimit_close` 가 JSON 파일에
    없던 이유. 이 시험은 "설정 전에 만든 로거 → 설정 후 스트림에 JSON" 을 못박는다.
    """
    import structlog

    structlog.reset_defaults()  # 모듈 로드 시점의 기본 설정을 흉내 낸다
    early = get_logger("made.before.configure")
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    try:
        early.info("LATE_BOUND", k=1)
    finally:
        configure_logging("INFO")
    lines = _lines(stream)
    assert lines and lines[0]["event_type"] == "LATE_BOUND"
    assert lines[0]["module"] == "made.before.configure"
    assert lines[0]["payload"] == {"k": 1}


def test_secrets_are_masked_in_logs(log_stream: io.StringIO) -> None:
    """DoD 4 — 로그에 시크릿 평문이 없어야 한다 (spec §8)."""
    get_logger("m").info("E", key=SecretStr("LEAK_ME"), nested={"k": SecretStr("ALSO_LEAK")})
    raw = log_stream.getvalue()
    assert "LEAK_ME" not in raw
    assert "ALSO_LEAK" not in raw
    assert "**********" in raw


def test_stdlib_logging_is_bridged_to_json(log_stream: io.StringIO) -> None:
    """라이브러리 로그(uvicorn, SQLAlchemy)도 같은 JSON 스키마로 렌더된다.

    평문으로 섞이면 로그 수집기가 그 라인만 조용히 버린다.

    Note:
        루트 로거로 실제 로그를 내지 않고 **포매터를 직접 검증**한다. pytest 의 로깅
        플러그인이 테스트 중 루트 핸들러를 가로채기 때문에, 스트림을 통한 검증은
        브릿지의 정상 여부와 무관하게 실패한다.
    """
    import logging

    handler = logging.getLogger().handlers[0]
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="서드파티 로그",
        args=None,
        exc_info=None,
    )

    entry = json.loads(handler.format(record))

    assert entry["event_type"] == "서드파티 로그"
    assert entry["level"] == "warning"
    assert "ts" in entry
    assert "trace_id" in entry
    assert log_stream is not None  # 픽스처가 브릿지를 설치했음을 명시


# ---------------------------------------------------------------------------
# 2. trace_id 전파 & 미들웨어
# ---------------------------------------------------------------------------


def test_trace_context_restores_previous_value() -> None:
    """잡이 끝나면 이전 trace_id 로 복원된다 — 다음 잡에 새지 않는다."""
    assert get_trace_id() is None
    with trace_context("aaaabbbb"):
        assert get_trace_id() == "aaaabbbb"
        with trace_context("ccccdddd"):
            assert get_trace_id() == "ccccdddd"
        assert get_trace_id() == "aaaabbbb"
    assert get_trace_id() is None


async def test_trace_id_survives_async_task_boundaries() -> None:
    """ContextVar 라서 태스크를 넘어 따라간다 — 인자로 끌고 다닐 필요가 없다."""

    async def inner() -> str | None:
        return get_trace_id()

    with trace_context("feedface"):
        assert await asyncio.create_task(inner()) == "feedface"


@pytest.mark.parametrize(
    ("header", "inherited"),
    [
        ("abcdef12", True),
        ("ABCDEF12", True),
        (None, False),
        ("", False),
        ("짧음", False),
        ("bad\nvalue", False),
        ("x" * 200, False),
    ],
)
def test_incoming_trace_id_is_validated_before_inheriting(
    header: str | None, inherited: bool
) -> None:
    """외부 헤더를 검증 없이 승계하면 로그 인젝션이 된다 (spec §8)."""
    resolved = resolve_incoming_trace_id(header)
    if inherited:
        assert resolved == (header or "").lower()
    else:
        assert resolved != header
        assert re.match(r"\A[0-9a-f]{32}\Z", resolved)


def test_middleware_binds_and_echoes_trace_id(log_stream: io.StringIO) -> None:
    """요청당 trace_id 발급 + 응답 헤더 반사 (P0-6-2).

    사용자가 오류를 신고할 때 이 값 하나로 전 구간 로그를 찾을 수 있어야 한다.
    """
    app = FastAPI()
    app.middleware("http")(trace_id_middleware)

    @app.get("/probe")
    def probe() -> dict[str, str | None]:  # pyright: ignore[reportUnusedFunction]
        get_logger("apps.api").info("PROBE_HANDLED")
        return {"trace_id": get_trace_id()}

    with TestClient(app) as client:
        response = cast(
            "httpx.Response",
            client.get("/probe", headers={TRACE_HEADER: "cafebabe"}),  # pyright: ignore[reportUnknownMemberType]
        )

    assert response.status_code == 200
    assert response.json()["trace_id"] == "cafebabe"
    assert response.headers[TRACE_HEADER] == "cafebabe"
    assert any(entry["trace_id"] == "cafebabe" for entry in _lines(log_stream))


# ---------------------------------------------------------------------------
# 3. 폴백 싱크
# ---------------------------------------------------------------------------


def _record(event_type: str = "E") -> EventRecord:
    return EventRecord(
        event_id=uuid.uuid4(),
        trace_id="t1",
        module="execution",
        level=LogLevel.ERROR,
        event_type=event_type,
        payload={"k": "v"},
    )


def test_fallback_appends_json_lines(tmp_path: Path) -> None:
    """한 줄이 한 레코드 — 마지막 줄이 깨져도 앞의 레코드는 살아남는다."""
    sink = FallbackSink(tmp_path / "fb.jsonl")
    sink.append(_record("A"))
    sink.append(_record("B"))
    assert [r.event_type for r in sink.read_all()] == ["A", "B"]
    assert len(sink.path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_fallback_round_trips_all_fields(tmp_path: Path) -> None:
    sink = FallbackSink(tmp_path / "fb.jsonl")
    original = _record("STOP_RAISED")
    sink.append(original)
    restored = sink.read_all()[0]
    assert restored.event_id == original.event_id
    assert restored.ts == original.ts
    assert restored.level is original.level
    assert restored.payload == original.payload


def test_fallback_raises_on_corrupt_line(tmp_path: Path) -> None:
    """깨진 줄을 조용히 건너뛰면 이관 배치가 레코드를 잃은 채 파일을 비운다 (spec §7)."""
    path = tmp_path / "fb.jsonl"
    path.write_text('{"broken": true}\n', encoding="utf-8")
    with pytest.raises(FallbackSinkError, match="파싱 실패"):
        FallbackSink(path).read_all()


# ---------------------------------------------------------------------------
# 4. DoD 1 · 6 · 7 — 실제 DB
# ---------------------------------------------------------------------------

pytest_db = pytest.mark.db


def _factory(url: str) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(create_engine(url))


@pytest.fixture
def db_url(migrated_test_database: str) -> str:
    """마이그레이션이 끝난 테스트 DB URL (`conftest.py`).

    Args:
        migrated_test_database: 테이블·롤까지 준비된 DB.

    Returns:
        접속 URL.

    Note:
        **직접 `DATABASE_URL` 을 읽어 `_test` 를 붙이지 않는다.** 그러면 테이블 생성을
        `test_migrations.py` 의 부수효과에 의존하게 되고, 빈 DB 에서는 알파벳 순서상
        이 파일이 먼저 돌아 `database does not exist` 로 깨진다 (P0-6 CI 실패 원인).
    """
    return migrated_test_database


@pytest_db
async def test_event_is_persisted_with_matching_trace_id(db_url: str, tmp_path: Path) -> None:
    """DoD 1 — 요청 1회 → `event_logs` 1행, **trace_id 일치**.

    `/health` 엔드포인트는 P0-9-1 이므로 여기서는 같은 경로(요청 → trace_id →
    event_logs)를 최소 앱으로 검증한다. `/health` 자체 확인은 P0-9-6 이다.
    """
    factory = _factory(db_url)
    logger = AuditLogger(PostgresEventSink(factory), FallbackSink(tmp_path / "fb.jsonl"))
    trace_id = new_trace_id()

    with trace_context(trace_id):
        attempt = await logger.record(
            event_type="HEALTH_CHECKED", module="apps.api", payload={"probe": True}
        )

    assert attempt.persisted is True, attempt.failure_reason

    engine = create_engine(db_url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text("SELECT trace_id, event_type FROM event_logs WHERE id = :i"),
                {"i": attempt.record.event_id},
            )
        ).one()
    await engine.dispose()

    assert row.trace_id == trace_id, "stdout 로그와 event_logs 의 trace_id 가 다르다"
    assert row.event_type == "HEALTH_CHECKED"


@pytest_db
async def test_db_down_executes_risk_reducing_and_recovers_later(
    db_url: str, tmp_path: Path
) -> None:
    """DoD 6·7 — DB 다운 시 집행 + 폴백 → 복구 후 이관 + 파일 비움.

    "DB 다운"을 **닿을 수 없는 포트**로 재현한다. 컨테이너를 실제로 내리면 병렬
    실행되는 다른 테스트를 깨뜨린다.
    """
    fallback = FallbackSink(tmp_path / "fb.jsonl")
    dead_url = re.sub(r":\d+/", ":1/", db_url)
    logger = AuditLogger(PostgresEventSink(_factory(dead_url)), fallback, notifier=_Silent())

    # DB 다운 — 손절은 집행되고 폴백에 남는다.
    attempt = await logger.record(
        event_type="STOP_LOSS_EXECUTED",
        module="execution.order_service",
        level=LogLevel.ERROR,
        risk=ActionRisk.RISK_REDUCING,
        position_id="pos_42",
    )
    assert attempt.may_proceed is True
    assert attempt.fell_back is True
    assert not fallback.is_empty()

    # DB 복구 — 이관 배치가 합집합을 하나로 만든다.
    result = await recover_fallback_logs(_factory(db_url), fallback)
    assert result.migrated == 1
    assert result.cleared is True
    assert fallback.is_empty()

    engine = create_engine(db_url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text("SELECT event_type, payload_json FROM event_logs WHERE id = :i"),
                {"i": attempt.record.event_id},
            )
        ).one()
    await engine.dispose()

    assert row.event_type == "STOP_LOSS_EXECUTED"
    assert row.payload_json["position_id"] == "pos_42"


@pytest_db
async def test_recovery_is_idempotent(db_url: str, tmp_path: Path) -> None:
    """이관 성공 후 파일 비우기가 실패한 경우를 대비한다 — 재실행이 중복을 만들지 않는다.

    `event_id` 를 애플리케이션에서 생성하는 이유가 이것이다.
    """
    fallback = FallbackSink(tmp_path / "fb.jsonl")
    record = _record("STOP_RAISED")
    fallback.append(record)
    fallback.append(record)  # 같은 id 를 두 번 — 비우기 실패 후 재기록 상황

    result = await recover_fallback_logs(_factory(db_url), fallback)
    assert result.read == 2

    engine = create_engine(db_url)
    async with engine.connect() as conn:
        count = (
            await conn.execute(
                sa.text("SELECT count(*) FROM event_logs WHERE id = :i"),
                {"i": record.event_id},
            )
        ).scalar_one()
    await engine.dispose()
    assert count == 1, "ON CONFLICT DO NOTHING 이 중복을 막지 못했다"


@pytest_db
async def test_recovery_preserves_file_when_migration_fails(tmp_path: Path) -> None:
    """이관 실패 시 폴백 파일을 **보존**한다.

    순서가 뒤바뀌면(비우기 → 이관) 레코드가 영구 유실되고, append-only 감사 로그를
    두는 이유 전체가 무너진다.
    """
    fallback = FallbackSink(tmp_path / "fb.jsonl")
    fallback.append(_record("STOP_LOSS_EXECUTED"))

    with pytest.raises(LogRecoveryError, match="보존"):
        await recover_fallback_logs(_factory("postgresql+psycopg://u:p@localhost:1/nope"), fallback)

    assert not fallback.is_empty(), "이관 실패인데 파일이 비었다 — 레코드가 유실됐다"
