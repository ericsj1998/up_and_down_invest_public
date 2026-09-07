"""요청당 trace_id 발급 미들웨어 (P0-6-2 · spec §4.14).

외부 헤더 승계 규칙: `X-Trace-Id` 가 오면 **승계**하고, 없으면 새로 발급한다.
승계가 필요한 이유는 프론트·다른 서비스에서 시작된 흐름을 이어야 하기 때문이다.

응답에도 `X-Trace-Id` 를 실어 준다 — 사용자가 오류를 신고할 때 이 값 하나로 전 구간
로그를 찾을 수 있다.

> ⚠️ FastAPI 앱에 이 미들웨어를 붙이는 것은 **P0-9-1**이다. 여기서는 미들웨어만 만든다.
"""

import re
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from updown.common.logging.context import new_trace_id, trace_context

TRACE_HEADER = "X-Trace-Id"

#: 승계 허용 형식. 외부 입력이므로 검증한다.
#:
#: 검증 없이 승계하면 로그 인젝션이 된다 — 개행이나 제어문자가 들어간 값이 JSON 로그의
#: 필드로 그대로 찍히면 로그 수집기가 오독한다 (spec §8).
_TRACE_ID_PATTERN = re.compile(r"\A[0-9a-fA-F]{8,64}\Z")


def resolve_incoming_trace_id(raw: str | None) -> str:
    """외부 헤더의 trace_id 를 검증해 승계하거나 새로 발급한다.

    Args:
        raw: `X-Trace-Id` 헤더 값. 없으면 None.

    Returns:
        사용할 trace_id.

    Note:
        형식이 어긋나면 **조용히 새 값을 발급**한다. 400 으로 거절하지 않는 이유:
        trace_id 는 관측 수단이지 기능 파라미터가 아니고, 잘못된 헤더 때문에 정상
        요청을 막을 이유가 없다. 대신 승계하지 않으므로 오염도 없다.
    """
    if raw and _TRACE_ID_PATTERN.match(raw):
        return raw.lower()
    return new_trace_id()


class TraceIdMiddleware:
    """요청 단위로 trace_id 를 바인딩하는 ASGI 미들웨어.

    Note:
        `BaseHTTPMiddleware` 대신 순수 ASGI 호출 규약을 쓰지 않고 Starlette 의
        `Request`/`Response` 를 쓰는 얇은 형태로 두었다 — P0-9-1 에서 FastAPI 에
        `@app.middleware("http")` 로 붙이거나 `add_middleware` 로 감싼다.
    """

    async def __call__(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """요청을 trace 컨텍스트로 감싼다.

        Args:
            request: 들어온 요청.
            call_next: 다음 핸들러.

        Returns:
            `X-Trace-Id` 가 실린 응답.
        """
        trace_id = resolve_incoming_trace_id(request.headers.get(TRACE_HEADER))
        with trace_context(trace_id):
            response = await call_next(request)
            response.headers[TRACE_HEADER] = trace_id
            return response


async def trace_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """함수형 미들웨어 — `@app.middleware("http")` 등록용.

    Args:
        request: 들어온 요청.
        call_next: 다음 핸들러.

    Returns:
        `X-Trace-Id` 가 실린 응답.
    """
    return await TraceIdMiddleware()(request, call_next)
