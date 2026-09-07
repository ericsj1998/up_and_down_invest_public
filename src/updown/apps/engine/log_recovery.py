"""폴백 파일 → `event_logs` 이관 배치 (P0-6-3c · spec §1.2.1, plan D-13).

DB 가 죽어 있는 동안 리스크 감소 행동은 폴백 파일에 기록됐다. 그 기간의 감사 추적은
**DB + 폴백 파일의 합집합**이며, 이 배치가 합쳐서 하나로 만든다.

## 순서가 안전을 결정한다

```
1) 폴백 파일 읽기
2) event_logs 로 INSERT (ON CONFLICT DO NOTHING)
3) **전부 성공한 뒤에만** 파일 비우기
```

**반대 순서(비우기 → 이관)는 절대 하지 않는다.** 이관 중 실패하면 레코드가 영구
유실되고, 그것은 append-only 감사 로그를 두는 이유 전체를 무너뜨린다.

`ON CONFLICT DO NOTHING` 이 재실행을 안전하게 만든다 — 이관은 성공했는데 파일 비우기가
실패한 경우, 다음 실행에서 같은 `event_id` 가 중복 삽입되지 않는다. 이것이 `event_id` 를
애플리케이션에서 생성하는 이유다 (`event_sink.py`).

> ⚠️ 스케줄러 등록은 **P0-9-7** 이다. 이 모듈은 배치 함수만 제공한다.
> 전용 롤 `updown_logrecovery` 로 접속한다 (0003 마이그레이션) — 애플리케이션 롤과
> 분리해야 "이관만 하는 경로"를 권한으로 증명할 수 있다.
"""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.logging.event_sink import dump_payload
from updown.common.logging.fallback_sink import FallbackSink

_INSERT = sa.text("""
    INSERT INTO event_logs (id, trace_id, module, level, event_type, payload_json, ts)
    VALUES (:id, :trace_id, :module, :level, :event_type, CAST(:payload AS JSONB), :ts)
    ON CONFLICT (id) DO NOTHING
""")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """이관 결과.

    Attributes:
        read: 폴백 파일에서 읽은 레코드 수.
        migrated: `event_logs` 에 넣기를 시도한 수.
        cleared: 파일을 비웠는가.
    """

    read: int
    migrated: int
    cleared: bool


class LogRecoveryError(RuntimeError):
    """이관 실패 — 폴백 파일은 **보존된다**.

    Note:
        이 예외가 났을 때 파일이 남아 있는 것이 정상이다. 다음 실행에서 재시도한다.
        **알림 대상**이다 — 이관이 계속 실패하면 감사 추적이 계속 쪼개진 상태로
        남는다 (spec §7).
    """


async def recover_fallback_logs(
    session_factory: async_sessionmaker[AsyncSession],
    fallback: FallbackSink,
) -> RecoveryResult:
    """폴백 파일의 이벤트를 `event_logs` 로 이관하고 파일을 비운다.

    Args:
        session_factory: **`updown_logrecovery` 롤**로 접속하는 세션 팩토리.
        fallback: 폴백 싱크.

    Returns:
        이관 결과.

    Raises:
        LogRecoveryError: 읽기 또는 삽입 실패. 파일은 비우지 않는다.

    Note:
        전 레코드를 **한 트랜잭션**에 넣는다. 부분 성공 후 파일을 비우면 남은 레코드가
        유실되고, 부분 성공 후 파일을 남기면 어디까지 갔는지 알 수 없다. 전부 되거나
        전부 안 되는 편이 판단이 쉽다.

        폴백 파일이 아주 커진 경우(장기 DB 장애)에는 배치 분할이 필요하다 — 그때는
        "성공한 레코드를 파일에서 지우는" 기능이 함께 있어야 하며, P2 운영 경험 후
        판단한다.
    """
    if fallback.is_empty():
        return RecoveryResult(read=0, migrated=0, cleared=False)

    try:
        records = fallback.read_all()
    except Exception as exc:
        raise LogRecoveryError(f"폴백 파일 읽기 실패 — 파일을 보존한다: {exc}") from exc

    if not records:
        return RecoveryResult(read=0, migrated=0, cleared=False)

    try:
        async with session_factory() as session:
            for record in records:
                await session.execute(
                    _INSERT,
                    {
                        "id": record.event_id,
                        "trace_id": record.trace_id,
                        "module": record.module,
                        "level": record.level.value,
                        "event_type": record.event_type,
                        "payload": dump_payload(record.payload),
                        "ts": record.ts,
                    },
                )
            await session.commit()
    except Exception as exc:
        raise LogRecoveryError(
            f"event_logs 이관 실패 — 폴백 파일 {fallback.path} 를 보존한다: {exc}"
        ) from exc

    # 이관이 커밋된 뒤에만 비운다.
    fallback.clear()
    return RecoveryResult(read=len(records), migrated=len(records), cleared=True)
