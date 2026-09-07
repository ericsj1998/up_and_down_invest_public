"""`event_logs` 적재 (spec §9, §4.14, §8).

**주 로직과 트랜잭션을 공유하지 않는다.** 같은 트랜잭션에 묶으면 로그 실패가 주 로직을
롤백시키고, 그것이 spec §1.2.1 이 금지하는 역전이다. 그래서 전용 세션을 쓴다.

`id` 를 **애플리케이션에서 생성**하는 이유: DB 적재가 실패하면 같은 레코드가 폴백
파일로 가고, 나중에 복구 배치가 이관한다. 그때 id 가 같아야 `ON CONFLICT DO NOTHING`
으로 중복을 막을 수 있다 — DB 기본값으로 두면 이관본이 새 행이 되어 중복이 쌓인다.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.db.base import JsonDict
from updown.common.db.models.enums import LogLevel


def _empty_payload() -> JsonDict:
    """빈 payload 기본값 — `dict` 를 그대로 넘기면 타입이 좁혀지지 않는다."""
    return {}


@dataclass(frozen=True, slots=True)
class EventRecord:
    """`event_logs` 한 행 (spec §9).

    Attributes:
        event_id: 행 id. **애플리케이션에서 생성**한다 (위 모듈 docstring 참조).
        trace_id: 요청·잡 단위 상관관계 ID (spec §4.14). **필수다** —
            `event_logs.trace_id` 가 NOT NULL 이며, trace_id 없는 감사 행은 역추적이
            불가능해 §4.14 의 목적을 잃는다. 컨텍스트에 없으면 `AuditLogger` 가 발급한다.
            (stdout 진단 로그는 다르다 — 그쪽은 없으면 `null` 로 남긴다.)
        module: 발생 모듈. 도메인 경계를 알 수 있게 적는다.
        level: 로그 레벨.
        event_type: 이벤트 종류. 예: `ORDER_SUBMITTED`, `STOP_RAISED`.
        payload: 상세. ID 체인의 나머지(`proposal_id` / `order_id` / `position_id`)는
            §9 에 컬럼이 없으므로 **이 안의 예약 키**로 들어간다 (spec §4.14).
        ts: 발생 시각 (UTC).
    """

    event_id: uuid.UUID
    trace_id: str
    module: str
    level: LogLevel
    event_type: str
    payload: JsonDict = field(default_factory=_empty_payload)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    actor: str | None = None
    """이 흐름을 **시작한 사람** (구글 이메일) — 2026-08-30 외부 공개 준비.

    🔴 `trace_id` 는 한 흐름을 잇지만 그 흐름을 시작한 사람은 안 말해 준다. 사람이
    여럿 들어오는 순간 *"이 주문 누가 냈지"* 를 답할 수 있어야 한다.

    ⛔ **None 일 수 있다** — 엔진의 스케줄 잡·부팅 로그는 사람이 시작한 것이 아니다.
    거기에 값을 채우면 "누가 했는지 아는 척" 이 된다.
    """

    def to_json_dict(self) -> JsonDict:
        """폴백 파일(JSON Lines)에 쓸 형태로 직렬화한다.

        Returns:
            JSON 직렬화 가능한 dict.
        """
        return {
            "event_id": str(self.event_id),
            "trace_id": self.trace_id,
            "module": self.module,
            "level": self.level.value,
            "event_type": self.event_type,
            "payload": self.payload,
            "ts": self.ts.isoformat(),
            # ⚠️ 폴백 파일에도 남긴다 — DB 가 죽었을 때 남는 유일한 기록이고,
            #    하필 그때가 "누가 했나" 를 제일 알고 싶은 순간이다.
            "actor": self.actor,
        }

    @classmethod
    def from_json_dict(cls, data: JsonDict) -> "EventRecord":
        """폴백 파일에서 읽은 dict 를 복원한다.

        Args:
            data: `to_json_dict` 가 만든 형태.

        Returns:
            복원된 레코드.
        """
        return cls(
            event_id=uuid.UUID(str(data["event_id"])),
            trace_id=str(data["trace_id"]),
            module=str(data["module"]),
            level=LogLevel(str(data["level"])),
            event_type=str(data["event_type"]),
            payload=data.get("payload") or {},
            ts=datetime.fromisoformat(str(data["ts"])),
        )


class EventSinkError(RuntimeError):
    """감사 로그 적재에 실패했다.

    Note:
        이 예외를 **주 로직으로 전파하지 않는다.** `AuditLogger` 가 잡아서 폴백과
        정책 판정으로 넘긴다 (spec §1.2.1).
    """


class EventSink(Protocol):
    """감사 이벤트 적재 대상."""

    async def emit(self, record: EventRecord) -> None:
        """레코드를 적재한다.

        Args:
            record: 적재할 이벤트.

        Raises:
            EventSinkError: 적재 실패.
        """
        ...


#: `event_logs` 는 append-only 다. `ON CONFLICT DO NOTHING` 은 UPDATE 가 아니라
#: **삽입 생략**이므로 append-only 권한(0003 마이그레이션)과 충돌하지 않는다.
_INSERT = sa.text("""
    INSERT INTO event_logs (id, trace_id, actor, module, level, event_type, payload_json, ts)
    VALUES (:id, :trace_id, :actor, :module, :level, :event_type, CAST(:payload AS JSONB), :ts)
    ON CONFLICT (id) DO NOTHING
""")


def dump_payload(payload: JsonDict) -> str:
    """Payload 를 JSONB 로 넣을 문자열로 만든다.

    Args:
        payload: 이벤트 상세.

    Returns:
        JSON 문자열.

    Note:
        `default=repr` 이 **시크릿 방어선**이다. 직렬화 불가 객체가 섞여도 예외로 죽지
        않고, `SecretStr` 은 `repr` 이 `**********` 이라 평문이 새지 않는다 (spec §8).
    """
    return json.dumps(payload, ensure_ascii=False, default=repr)


class PostgresEventSink:
    """`event_logs` 테이블 적재 (spec §9).

    Note:
        애플리케이션 롤은 이 테이블에 `SELECT, INSERT` 권한만 갖는다 (0003 마이그레이션,
        절대 규칙 #8-2). UPDATE/DELETE 를 시도하면 런타임 권한 오류다 — 그래서 이
        싱크에는 갱신 경로가 아예 없다.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """싱크를 만든다.

        Args:
            session_factory: **전용** 세션 팩토리. 주 로직과 트랜잭션을 공유하면
                로그 실패가 주 로직을 롤백시킨다 (spec §1.2.1).
        """
        self._session_factory = session_factory

    async def emit(self, record: EventRecord) -> None:
        """레코드를 INSERT 한다.

        Args:
            record: 적재할 이벤트.

        Raises:
            EventSinkError: DB 연결·권한·제약 오류 등 모든 실패를 감싼다.

        Note:
            예외 타입을 하나로 좁히는 이유: 호출부(`AuditLogger`)의 관심사는 "적재가
            됐는가" 뿐이고, 실패 원인별 분기는 없다. 원인은 메시지에 담아 알림으로
            보낸다 (spec §4.12).
        """
        try:
            async with self._session_factory() as session:
                await session.execute(
                    _INSERT,
                    {
                        "id": record.event_id,
                        "trace_id": record.trace_id,
                        "actor": record.actor,
                        "module": record.module,
                        "level": record.level.value,
                        "event_type": record.event_type,
                        "payload": dump_payload(record.payload),
                        "ts": record.ts,
                    },
                )
                await session.commit()
        except Exception as exc:
            raise EventSinkError(f"event_logs 적재 실패: {exc}") from exc
