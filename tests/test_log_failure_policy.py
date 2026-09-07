"""로그 적재 실패 정책 (P0-6-3b · spec §1.2.1 · plan D-13) ⭐.

**이 파일이 D-13 의 안전망이다.** 여기가 통과하는 한, 로그 DB 장애가 손절을 막지 못한다.

DoD 6 — DB 를 내린 상태에서:
- 리스크 감소 행동은 **집행되고** 폴백 파일에 기록된다
- 리스크 증가 행동은 **보류된다**
- 로그 실패 알림이 발생한다
"""

from pathlib import Path

import pytest

from updown.common.db.models.enums import LogLevel
from updown.common.logging.audit import (
    SYNTHESIZED_TRACE_KEY,
    AuditLogger,
    LogAttempt,
    LogHealthState,
    StderrLogFailureNotifier,
)
from updown.common.logging.context import trace_context
from updown.common.logging.event_sink import EventRecord, EventSinkError
from updown.common.logging.fallback_sink import FallbackSink
from updown.common.logging.policy import (
    DEFAULT_ACTION_RISK,
    ActionRisk,
    must_proceed_despite_log_failure,
    requires_fallback_persistence,
)


class _FailingSink:
    """항상 실패하는 싱크 — DB 다운과 동등하다."""

    def __init__(self, reason: str = "connection refused") -> None:
        self.reason = reason
        self.calls = 0

    async def emit(self, record: EventRecord) -> None:  # noqa: ARG002
        self.calls += 1
        raise EventSinkError(self.reason)


class _ExplodingSink:
    """`EventSinkError` 가 아닌 예외를 던지는 싱크."""

    async def emit(self, record: EventRecord) -> None:  # noqa: ARG002
        raise MemoryError("싱크가 감싸지 못한 예외")


class _OkSink:
    def __init__(self) -> None:
        self.records: list[EventRecord] = []

    async def emit(self, record: EventRecord) -> None:
        self.records.append(record)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.attempts: list[LogAttempt] = []

    def notify(self, attempt: LogAttempt) -> None:
        self.attempts.append(attempt)


class _BrokenNotifier:
    def notify(self, attempt: LogAttempt) -> None:  # noqa: ARG002
        raise RuntimeError("알림 경로도 죽었다")


@pytest.fixture
def fallback(tmp_path: Path) -> FallbackSink:
    return FallbackSink(tmp_path / "fallback.jsonl")


# ---------------------------------------------------------------------------
# 1. 순수 판정 (policy.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (ActionRisk.RISK_REDUCING, True),
        (ActionRisk.RISK_INCREASING, False),
        (ActionRisk.READ_ONLY, False),
        (None, False),
    ],
)
def test_only_risk_reducing_proceeds_without_a_log(risk: ActionRisk | None, expected: bool) -> None:
    """spec §1.2.1 표 그대로 — 리스크 감소만 무조건 집행이다."""
    assert must_proceed_despite_log_failure(risk) is expected


def test_unclassified_action_defaults_to_hold() -> None:
    """**미선언 시 기본값이 보류**여야 한다 (spec §1.2.1).

    이 상수의 방향이 정책 전체의 안전성을 결정한다. `RISK_REDUCING` 이 기본값이면
    새 행동이 분류 없이 무단 집행된다.
    """
    assert DEFAULT_ACTION_RISK is ActionRisk.RISK_INCREASING
    assert must_proceed_despite_log_failure(None) is False


def test_executed_actions_always_get_a_fallback_record() -> None:
    """집행되는 행동은 반드시 폴백에 남는다 — 그래야 감사 추적에 구멍이 없다."""
    for risk in ActionRisk:
        assert requires_fallback_persistence(risk) == must_proceed_despite_log_failure(risk)


# ---------------------------------------------------------------------------
# 2. DoD 6 — 적재 실패 시의 행동 (audit.py)
# ---------------------------------------------------------------------------


async def test_stop_loss_is_executed_and_recorded_when_db_is_down(
    fallback: FallbackSink,
) -> None:
    """DoD 6 — 손절은 로그가 실패해도 집행되고 폴백에 남는다.

    **최상위 원칙의 검증이다**: 손절 집행은 무슨 일이 있어도 막히지 않는다.
    """
    notifier = _RecordingNotifier()
    logger = AuditLogger(_FailingSink(), fallback, notifier=notifier)

    attempt = await logger.record(
        event_type="STOP_LOSS_EXECUTED",
        module="execution.order_service",
        risk=ActionRisk.RISK_REDUCING,
        payload={"symbol": "KRW-BTC"},
        position_id="pos_1",
    )

    assert attempt.may_proceed is True, "손절이 로그 실패로 막혔다 — §1.2.1 위반"
    assert attempt.persisted is False
    assert attempt.fell_back is True
    assert attempt.degraded is True

    records = fallback.read_all()
    assert len(records) == 1
    assert records[0].event_type == "STOP_LOSS_EXECUTED"
    assert records[0].payload["position_id"] == "pos_1"
    assert notifier.attempts, "로그 실패 알림이 발생하지 않았다 (spec §7)"


async def test_new_entry_is_held_when_db_is_down(fallback: FallbackSink) -> None:
    """DoD 6 — 신규 진입은 로그 실패 시 보류된다."""
    notifier = _RecordingNotifier()
    logger = AuditLogger(_FailingSink(), fallback, notifier=notifier)

    attempt = await logger.record(
        event_type="ORDER_SUBMITTED",
        module="execution.order_service",
        risk=ActionRisk.RISK_INCREASING,
    )

    assert attempt.may_proceed is False
    assert attempt.fell_back is False, "보류된 행동은 일어나지 않았으므로 폴백이 없어야 한다"
    assert notifier.attempts, "보류돼도 실패 자체는 알려야 한다"


async def test_omitting_risk_holds_the_action(fallback: FallbackSink) -> None:
    """분류를 명시하지 않으면 보류된다 — API 기본값이 안전한 쪽이다."""
    logger = AuditLogger(_FailingSink(), fallback, notifier=_RecordingNotifier())
    attempt = await logger.record(event_type="SOMETHING_NEW", module="analysis.detectors")
    assert attempt.may_proceed is False


async def test_log_failure_is_always_notified(fallback: FallbackSink) -> None:
    """분류와 무관하게 로그 실패는 알림 대상이다 (spec §4.12, §7)."""
    notifier = _RecordingNotifier()
    logger = AuditLogger(_FailingSink(), fallback, notifier=notifier)
    for risk in ActionRisk:
        await logger.record(event_type="E", module="m", risk=risk)
    assert len(notifier.attempts) == len(ActionRisk)


async def test_record_never_raises_even_when_everything_fails(tmp_path: Path) -> None:
    """`record()` 는 예외를 던지지 않는다.

    적재 실패로 예외가 올라가면 호출부의 정상 경로가 끊기고, 그것이 곧 "로그 실패가
    손절을 막는" 상황이다 (spec §1.2.1).
    """
    # 폴백 경로를 디렉터리로 만들어 쓰기를 실패시킨다.
    blocked = tmp_path / "blocked.jsonl"
    blocked.mkdir()
    logger = AuditLogger(_FailingSink(), FallbackSink(blocked), notifier=_BrokenNotifier())

    attempt = await logger.record(
        event_type="STOP_LOSS_EXECUTED", module="execution", risk=ActionRisk.RISK_REDUCING
    )

    assert attempt.may_proceed is True, "폴백·알림이 다 죽어도 손절은 집행되어야 한다"
    assert attempt.fell_back is False
    assert attempt.failure_reason is not None
    assert "폴백도 실패" in attempt.failure_reason


async def test_unexpected_sink_exception_still_follows_policy(fallback: FallbackSink) -> None:
    """싱크가 `EventSinkError` 로 감싸지 못한 예외도 정책을 타야 한다."""
    logger = AuditLogger(_ExplodingSink(), fallback, notifier=_RecordingNotifier())
    attempt = await logger.record(
        event_type="POSITION_CLOSED", module="execution", risk=ActionRisk.RISK_REDUCING
    )
    assert attempt.may_proceed is True
    assert attempt.fell_back is True


# ---------------------------------------------------------------------------
# 3. 정상 경로 + 건강 상태
# ---------------------------------------------------------------------------


async def test_successful_record_proceeds_and_leaves_no_fallback(
    fallback: FallbackSink,
) -> None:
    sink = _OkSink()
    logger = AuditLogger(sink, fallback)
    with trace_context("abc123ff"):
        attempt = await logger.record(event_type="CANDLE_BACKFILL_DONE", module="marketdata.ingest")
    assert attempt.persisted is True
    assert attempt.may_proceed is True
    assert attempt.degraded is False
    assert fallback.is_empty()
    assert sink.records[0].trace_id == "abc123ff"


async def test_health_state_tracks_failures_then_recovers(fallback: FallbackSink) -> None:
    """`/health` 가 읽을 상태 — 로그가 죽은 것을 로그로만 알리면 아무도 모른다 (§12.6)."""
    health = LogHealthState()
    failing = AuditLogger(_FailingSink(), fallback, notifier=_RecordingNotifier(), health=health)
    await failing.record(event_type="E", module="m", risk=ActionRisk.RISK_REDUCING)
    await failing.record(event_type="E", module="m", risk=ActionRisk.RISK_REDUCING)

    assert health.degraded is True
    assert health.consecutive_failures == 2
    assert health.last_failure_at is not None
    assert health.last_failure_reason is not None

    ok = AuditLogger(_OkSink(), fallback, health=health)
    await ok.record(event_type="E", module="m")
    assert health.degraded is False
    assert health.consecutive_failures == 0


async def test_trace_id_is_minted_and_flagged_outside_any_context(
    fallback: FallbackSink,
) -> None:
    """감사 행은 trace_id 가 필수다 — 없으면 발급하고 **고립 사실을 남긴다**.

    `event_logs.trace_id` 가 NOT NULL 이라서만이 아니다. trace_id 없는 감사 행은
    역추적이 불가능해 §4.14 의 목적을 잃는다. 다만 발급된 값은 다른 로그와 이어지지
    않으므로, 그 사실을 표시해 두지 않으면 나중에 원인을 조사하게 된다.
    """
    sink = _OkSink()
    logger = AuditLogger(sink, fallback)
    attempt = await logger.record(event_type="BOOT_COMPLETED", module="apps.engine.main")

    assert attempt.record.trace_id, "감사 행에 trace_id 가 없다"
    assert attempt.record.payload[SYNTHESIZED_TRACE_KEY] is True


async def test_trace_id_is_not_flagged_inside_a_context(fallback: FallbackSink) -> None:
    """컨텍스트가 있으면 승계하고 표시하지 않는다."""
    logger = AuditLogger(_OkSink(), fallback)
    with trace_context("aabbccdd"):
        attempt = await logger.record(event_type="JOB_STARTED", module="apps.engine.scheduler")
    assert attempt.record.trace_id == "aabbccdd"
    assert SYNTHESIZED_TRACE_KEY not in attempt.record.payload


def test_stderr_notifier_bypasses_the_log_pipeline(capsys: pytest.CaptureFixture[str]) -> None:
    """실패 통지는 structlog 을 타지 않는다 — 고장난 경로로 통지하면 함께 죽는다."""
    record = EventRecord(
        event_id=__import__("uuid").uuid4(),
        trace_id="t1",
        module="execution",
        level=LogLevel.ERROR,
        event_type="STOP_LOSS_EXECUTED",
    )
    StderrLogFailureNotifier().notify(
        LogAttempt(
            record=record,
            persisted=False,
            fell_back=True,
            may_proceed=True,
            failure_reason="connection refused",
        )
    )
    captured = capsys.readouterr()
    assert "AUDIT-LOG-FAILURE" in captured.err
    assert "STOP_LOSS_EXECUTED" in captured.err
    assert "집행됨" in captured.err
