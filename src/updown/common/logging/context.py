"""trace_id 전파 (spec §4.14).

`trace_id` 는 **요청 또는 잡 실행 1회**의 범위다. `ContextVar` 에 두면 async 태스크
경계를 넘어 자동으로 따라가므로, 모든 함수에 인자로 끌고 다닐 필요가 없다.

ID 체인 `trace_id → proposal_id → order_id → position_id`(§4.14)에서 **trace_id 만
ContextVar** 다. 나머지는 특정 제안·주문에 매인 값이라 이벤트별로 명시하는 것이
정확하다 — 컨텍스트에 두면 어느 제안의 것인지 헷갈리는 지점이 생긴다.
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_trace_id: ContextVar[str | None] = ContextVar("updown_trace_id", default=None)

_actor: ContextVar[str | None] = ContextVar("updown_actor", default=None)
"""**누가** 이 요청을 보냈나 (구글 이메일) — 2026-08-30 외부 공개 준비.

🔴 사람이 여럿 들어오는 순간 *"이 주문 누가 냈지"* 를 답할 수 있어야 한다.
`trace_id` 는 **한 흐름**을 잇지만 그 흐름을 시작한 사람은 안 말해 준다.

⚠️ `trace_id` 와 같은 이유로 `ContextVar` 다 — 미들웨어에서 한 번 넣으면 async 경계를
넘어 따라간다. 로그를 남기는 모든 함수에 인자로 끌고 다닐 수는 없다.

⛔ **None 일 수 있다는 것이 중요하다.** 엔진의 스케줄 잡·부팅 로그는 사람이 시작한
것이 아니다. 거기에 가짜 값을 채우면 *"누가 했는지 아는 척"* 이 된다.
"""


def get_actor() -> str | None:
    """이 흐름을 시작한 사람.

    Returns:
        이메일. 없으면 None — 기계가 한 일(스케줄 잡·부팅)이고, 가짜 값을 채우지 않는다.
    """
    return _actor.get()


@contextmanager
def actor_context(actor: str | None) -> Generator[str | None]:
    """블록 동안 행위자를 유지한다.

    Args:
        actor: 이메일. None 이면 **기계가 한 일**로 남는다.

    Yields:
        그 값 그대로.

    Note:
        ⚠️ 끝나면 자동 복원된다 — 다음 요청의 로그에 앞사람 이메일이 새면 감사 기록이
        거짓말이 되고, 그건 기록이 없는 것보다 나쁘다.
    """
    token = _actor.set(actor)
    try:
        yield actor
    finally:
        _actor.reset(token)


def new_trace_id() -> str:
    """새 trace_id 를 만든다.

    Returns:
        32자 hex 문자열.

    Note:
        UUID4 의 hex 표현을 쓴다 — 하이픈이 없어 로그 grep·URL 전달이 편하고,
        브로커 멱등키(§4.10)와 섞여도 형식이 구분된다.
    """
    return uuid.uuid4().hex


def get_trace_id() -> str | None:
    """현재 컨텍스트의 trace_id.

    Returns:
        설정돼 있으면 trace_id, 없으면 None.

    Note:
        None 을 반환할 수 있다는 것이 중요하다. 부팅 로그처럼 요청·잡 밖에서 나는
        로그가 있고, 그때 가짜 trace_id 를 만들면 "추적 가능한 척"이 된다.
    """
    return _trace_id.get()


def set_trace_id(trace_id: str) -> Token[str | None]:
    """trace_id 를 설정한다.

    Args:
        trace_id: 설정할 값.

    Returns:
        복원용 토큰.

    Note:
        미들웨어처럼 컨텍스트 진입/이탈이 분리된 곳에서 쓴다. 블록 범위로 충분하면
        `trace_context()` 가 낫다 — 복원을 잊을 수 없다.
    """
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """`set_trace_id` 이전 상태로 되돌린다.

    Args:
        token: `set_trace_id` 가 준 토큰.
    """
    _trace_id.reset(token)


@contextmanager
def trace_context(trace_id: str | None = None) -> Generator[str]:
    """블록 동안 trace_id 를 유지한다.

    Args:
        trace_id: 승계할 값. None 이면 새로 발급한다.

    Yields:
        이 블록에서 유효한 trace_id.

    Note:
        engine 의 **스케줄 잡 실행 단위마다** 이것으로 감싼다 (spec §4.14) —
        웹 요청 없는 경로도 추적 가능해야 하기 때문이다. 잡이 끝나면 자동 복원되므로
        다음 잡의 로그에 이전 잡의 trace_id 가 새지 않는다.
    """
    resolved = trace_id or new_trace_id()
    token = _trace_id.set(resolved)
    try:
        yield resolved
    finally:
        _trace_id.reset(token)
