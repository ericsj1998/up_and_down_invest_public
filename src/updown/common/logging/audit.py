"""감사 로그 조립 — 적재 → 폴백 → 알림 → 판정 (spec §1.2.1, plan D-13) ⭐.

계획의 파일 목록에는 없는 모듈이다. `policy.py`(순수 판정)와 `event_sink.py`(적재)의
역할을 지키면서 **넷을 순서대로 엮는 곳**이 필요했다 — 어느 한쪽에 넣으면 그 모듈이
"적재 + 폴백 + 알림 + 판정"을 다 하게 되어 책임이 흐려진다.

## 순서가 정책이다

```
1) event_logs INSERT 시도
   └─ 성공 → 끝. 행동은 진행 가능
2) 실패
   ├─ 리스크 감소 행동?  → 폴백 파일에 기록 → **집행 허용**
   ├─ 그 외             → 폴백 불필요      → **보류**
   └─ 어느 경우든 **로그 실패 자체를 알린다** (§4.12, §7)
```

**로그 실패 알림은 분류와 무관하다.** 조용한 로그 유실은 spec §7 "조용한 실패 금지"
위반이며, 보류된 경우에도 "왜 진입이 안 됐는지"를 알아야 한다.
"""

import contextlib
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from updown.common.db.base import JsonDict
from updown.common.db.models.enums import LogLevel
from updown.common.logging.context import get_actor, get_trace_id, new_trace_id
from updown.common.logging.event_sink import EventRecord, EventSink, EventSinkError
from updown.common.logging.fallback_sink import FallbackSink, FallbackSinkError
from updown.common.logging.policy import (
    DEFAULT_ACTION_RISK,
    ActionRisk,
    must_proceed_despite_log_failure,
    requires_fallback_persistence,
)

#: `payload` 안에서 ID 체인에 예약된 키 (spec §4.14).
#:
#: §9 `event_logs` 에는 `trace_id` 컬럼만 있어 나머지는 payload 로 들어간다.
#: 키 이름을 고정해야 나중에 손실 귀속 리포트가 조인할 수 있다.
CHAIN_KEYS = ("proposal_id", "order_id", "position_id")

#: trace 컨텍스트 밖에서 남긴 감사 이벤트임을 표시하는 payload 키.
#:
#: `event_logs.trace_id` 는 NOT NULL 이므로 컨텍스트가 없으면 발급해야 한다. 그런데
#: 발급된 trace_id 는 **다른 어떤 로그와도 이어지지 않는다** — 그 사실을 남겨 두지
#: 않으면 나중에 "왜 이 이벤트만 고립돼 있지?"를 조사하게 된다.
SYNTHESIZED_TRACE_KEY = "trace_synthesized"


@dataclass(frozen=True, slots=True)
class LogAttempt:
    """적재 시도 결과 (spec §1.2.1).

    Attributes:
        record: 시도한 레코드.
        persisted: `event_logs` 에 들어갔는가.
        fell_back: 폴백 파일에 기록됐는가.
        may_proceed: **호출부가 확인해야 하는 값** — 행동을 계속해도 되는가.
        failure_reason: 적재 실패 사유. 성공이면 None.

    Note:
        `may_proceed` 를 반환값으로 노출하는 이유: 파이썬은 "이 값을 확인해라"를 강제할
        수 없지만, 결과 객체를 받아 놓고 무시하는 코드는 리뷰에서 눈에 띈다.
        `bool` 하나만 돌려주면 그냥 버려진다.
    """

    record: EventRecord
    persisted: bool
    fell_back: bool
    may_proceed: bool
    failure_reason: str | None = None

    @property
    def degraded(self) -> bool:
        """감사 추적이 온전하지 않은 상태인가.

        Note:
            True 면 감사 추적은 **DB + 폴백 파일의 합집합**이다. 복구 배치가 이관하기
            전까지는 `event_logs` 만 조회하면 일부 이벤트가 보이지 않는다
            (docs/platform/logging_conventions.md).
        """
        return not self.persisted


class LogFailureNotifier(Protocol):
    """로그 적재 실패를 알리는 경로 (spec §4.12).

    Note:
        P2-5 에서 실제 알림 채널(이메일·푸시)로 교체된다. 그때까지는 stderr 다.
    """

    def notify(self, attempt: LogAttempt) -> None:
        """실패를 통지한다.

        Args:
            attempt: 실패한 적재 시도.
        """
        ...


class LogHealthState:
    """로그 적재 건강 상태 — `/health` 가 읽는다 (P0-9-1).

    Note:
        데드맨 스위치(§12.6)와 같은 발상이다. 로그가 죽은 것을 **로그로만** 알리면
        아무도 모른다. `/health` 가 상태를 드러내야 외부 헬스체크가 감지할 수 있다.
    """

    def __init__(self) -> None:
        """초기 상태(정상)로 만든다."""
        self.consecutive_failures = 0
        self.last_failure_at: datetime | None = None
        self.last_failure_reason: str | None = None

    @property
    def degraded(self) -> bool:
        """직전 적재가 실패한 상태인가."""
        return self.consecutive_failures > 0

    def record_failure(self, reason: str) -> None:
        """실패를 기록한다.

        Args:
            reason: 실패 사유.
        """
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now(UTC)
        self.last_failure_reason = reason

    def record_success(self) -> None:
        """성공을 기록해 카운터를 되돌린다."""
        self.consecutive_failures = 0


class StderrLogFailureNotifier:
    """stderr 통지 — P2-5 알림 연결 전까지의 임시 경로.

    Note:
        **structlog 을 쓰지 않는다.** 로그 경로가 고장난 상황을 알리는 통지가 같은
        로그 경로를 타면 함께 죽는다. 그래서 `sys.stderr` 에 직접 쓴다.
    """

    def notify(self, attempt: LogAttempt) -> None:
        """실패를 stderr 에 쓴다.

        Args:
            attempt: 실패한 적재 시도.
        """
        state = "집행됨(폴백 기록)" if attempt.may_proceed else "보류됨"
        print(
            "[AUDIT-LOG-FAILURE] "
            f"event_type={attempt.record.event_type} "
            f"trace_id={attempt.record.trace_id} "
            f"행동={state} "
            f"폴백={'기록' if attempt.fell_back else '미기록'} "
            f"사유={attempt.failure_reason}",
            file=sys.stderr,
            flush=True,
        )


class AuditLogger:
    """감사 이벤트를 남기고, 실패 시 정책에 따라 집행/보류를 판정한다 (spec §1.2.1)."""

    def __init__(
        self,
        sink: EventSink,
        fallback: FallbackSink,
        *,
        notifier: LogFailureNotifier | None = None,
        health: LogHealthState | None = None,
    ) -> None:
        """감사 로거를 만든다.

        Args:
            sink: `event_logs` 적재 싱크.
            fallback: 폴백 파일 싱크.
            notifier: 실패 통지 경로. 기본은 stderr.
            health: 건강 상태 홀더. `/health` 와 공유한다.
        """
        self._sink = sink
        self._fallback = fallback
        self._notifier = notifier or StderrLogFailureNotifier()
        self._health = health or LogHealthState()

    @property
    def health(self) -> LogHealthState:
        """건강 상태 — `/health` 가 읽는다."""
        return self._health

    async def record(
        self,
        *,
        event_type: str,
        module: str,
        payload: JsonDict | None = None,
        level: LogLevel = LogLevel.INFO,
        risk: ActionRisk = DEFAULT_ACTION_RISK,
        proposal_id: str | None = None,
        order_id: str | None = None,
        position_id: str | None = None,
    ) -> LogAttempt:
        """이벤트를 적재하고 행동 진행 가능 여부를 판정한다.

        Args:
            event_type: 이벤트 종류 (`docs/platform/logging_conventions.md` 규약).
            module: 발생 모듈.
            payload: 상세.
            level: 로그 레벨.
            risk: 행동의 리스크 방향. **생략하면 `RISK_INCREASING`(보류)** 이다 —
                분류를 잊었을 때 안전한 쪽으로 떨어지게 한 것이다 (spec §1.2.1).
            proposal_id: ID 체인 (spec §4.14).
            order_id: ID 체인.
            position_id: ID 체인.

        Returns:
            시도 결과. **호출부는 `may_proceed` 를 반드시 확인한다.**

        Note:
            이 메서드는 **예외를 던지지 않는다.** 적재 실패로 예외가 올라가면 호출부의
            정상 경로가 끊기고, 그것이 곧 "로그 실패가 손절을 막는" 상황이다
            (spec §1.2.1). 실패는 반환값으로만 표현한다.
        """
        merged = self._merge_chain(payload, proposal_id, order_id, position_id)

        # 감사 행은 trace_id 가 필수다 (`event_logs.trace_id` NOT NULL). 컨텍스트가
        # 없으면 발급하되, 고립된 이벤트임을 payload 에 남긴다.
        trace_id = get_trace_id()
        if trace_id is None:
            trace_id = new_trace_id()
            merged[SYNTHESIZED_TRACE_KEY] = True

        record = EventRecord(
            event_id=uuid.uuid4(),
            trace_id=trace_id,
            module=module,
            level=level,
            event_type=event_type,
            payload=merged,
            # 🔴 **누가 시작한 흐름인가** (2026-08-30 외부 공개 준비). `trace_id` 는
            #    한 흐름을 잇지만 그 흐름을 시작한 사람은 안 말해 준다 — 사람이
            #    여럿 들어오면 *"이 주문 누가 냈지"* 를 답할 수 있어야 한다.
            #
            # ⛔ 없으면 None 그대로 둔다. 엔진의 스케줄 잡은 사람이 시작한 것이
            #    아니고, 거기에 값을 채우면 "누가 했는지 아는 척" 이 된다.
            actor=get_actor(),
        )

        try:
            await self._sink.emit(record)
        except EventSinkError as exc:
            return self._handle_failure(record, risk, str(exc))
        except Exception as exc:  # 싱크가 감싸지 못한 예외도 정책을 타야 한다
            return self._handle_failure(record, risk, f"예상치 못한 적재 오류: {exc}")

        self._health.record_success()
        return LogAttempt(record=record, persisted=True, fell_back=False, may_proceed=True)

    def _handle_failure(self, record: EventRecord, risk: ActionRisk, reason: str) -> LogAttempt:
        """적재 실패를 정책에 따라 처리한다 (spec §1.2.1)."""
        may_proceed = must_proceed_despite_log_failure(risk)
        fell_back = False

        if requires_fallback_persistence(risk):
            try:
                self._fallback.append(record)
                fell_back = True
            except FallbackSinkError as exc:
                # 폴백까지 실패했다. **그래도 리스크 감소 행동은 집행한다** —
                # 기록 실패가 손절을 막는 것이 이 정책이 막으려는 상황이다.
                reason = f"{reason} / 폴백도 실패: {exc}"

        self._health.record_failure(reason)
        attempt = LogAttempt(
            record=record,
            persisted=False,
            fell_back=fell_back,
            may_proceed=may_proceed,
            failure_reason=reason,
        )
        # 알림 경로가 죽어도 판정 결과는 돌려줘야 한다 — 통지 실패가 손절을 막으면
        # 그것이 이 정책이 막으려는 상황의 재발이다.
        with contextlib.suppress(Exception):
            self._notifier.notify(attempt)
        return attempt

    @staticmethod
    def _merge_chain(
        payload: JsonDict | None,
        proposal_id: str | None,
        order_id: str | None,
        position_id: str | None,
    ) -> JsonDict:
        """ID 체인을 payload 예약 키에 넣는다 (spec §4.14)."""
        merged: JsonDict = dict(payload or {})
        for key, value in zip(CHAIN_KEYS, (proposal_id, order_id, position_id), strict=True):
            if value is not None:
                merged[key] = value
        return merged
