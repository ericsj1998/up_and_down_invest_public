r"""Gate.io v4 요청 서명 (APIv4 HMAC-SHA512).

## 🔴 순수 함수만 둔다

서명은 이 프로젝트에서 **틀리면 아무것도 안 되는데 왜 안 되는지 알기 어려운** 부분이다
(401 만 돌아온다). 그래서 계산을 I/O 와 떼어 놓고, 문서에 실린 **정답 벡터**로 잠근다 —
문자열 하나가 어긋나도 테스트가 잡는다.

## 규격 (docs/providers/gate_api.md)

    서명 문자열 = METHOD + "\\n" + URL + "\\n" + QUERY + "\\n"
                  + HexEncode(SHA512(body)) + "\\n" + TIMESTAMP

    서명 = HexEncode(HMAC-SHA512(secret, 서명 문자열))

⚠️ 함정 넷 — 넷 다 401 로만 드러난다:

1. **URL 에 `/api/v4` 가 들어간다.** 우리 클라이언트의 `base_url` 이 이미
   `.../api/v4` 라서, httpx 가 보는 경로(`/futures/usdt/accounts`)와 서명에 넣을 경로
   (`/api/v4/futures/usdt/accounts`)가 **다르다**.
2. **쿼리는 URL 인코딩하지 않고**, 실제 URL 에 붙은 **순서 그대로** 넣는다.
   dict 를 정렬하면 순서가 달라져 서명이 깨진다.
3. **본문이 없으면 빈 문자열의 SHA512** 를 쓴다 (0 이나 빈 해시가 아니다).
4. **타임스탬프는 초**다. 밀리초를 넣으면 서버 시계와 어긋난 것으로 본다.
"""

import hashlib
import hmac
from typing import Final
from urllib.parse import urlencode

EMPTY_BODY_SHA512: Final = (
    "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
    "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
)
"""빈 문자열의 SHA512 (Gate 문서에 그대로 실려 있다).

⛔ 본문이 없을 때 이것을 쓴다. 빈 해시나 `""` 를 넣으면 401 이다 — 문서가 값을 직접
적어 둔 이유가 그것이다.
"""

API_PREFIX: Final = "/api/v4"
"""서명에 들어가는 경로 접두.

🔴 **클라이언트의 `base_url` 에 이미 포함돼 있다.** httpx 에 넘기는 경로는
`/futures/usdt/accounts` 이고 서명에 넣을 것은 `/api/v4/futures/usdt/accounts` 다 —
이 둘을 같게 쓰면 서명이 늘 틀리고 증상은 401 뿐이다.
"""


def body_hash(body: str) -> str:
    """본문의 SHA512 hex.

    Args:
        body: 요청 본문. 없으면 빈 문자열.

    Returns:
        hex 소문자.

    Note:
        빈 본문도 **해시한다** (결과가 `EMPTY_BODY_SHA512`). 특별 취급하지 않으므로
        호출부가 분기할 필요가 없다.
    """
    return hashlib.sha512(body.encode("utf-8")).hexdigest()


def query_string(params: dict[str, str] | None) -> str:
    """서명에 넣을 쿼리 문자열.

    Args:
        params: 쿼리 파라미터. None 이나 빈 dict 면 빈 문자열.

    Returns:
        `a=1&b=2`. **URL 인코딩하지 않는다.**

    Note:
        🔴 **dict 삽입 순서를 지킨다.** Gate 는 *"실제 URL 에 붙은 순서 그대로"* 를
        요구하므로 정렬하면 서명이 깨진다. Python dict 가 삽입 순서를 보장하니
        호출부가 URL 에 넣는 순서로 만들면 그대로 맞는다.

        ⚠️ 인코딩하지 않는 것이 규격이다 — `urlencode` 를 쓰되 `quote_via` 를
        항등으로 두지 않고, 값에 특수문자가 없다는 전제를 **여기 적어 둔다**.
        계약 이름·간격·숫자만 넘기므로 지금은 안전하다. 심볼에 `&` 나 `=` 가 들어오는
        거래소가 생기면 여기가 깨지고, 그때는 서명 규격을 다시 읽어야 한다.
    """
    if not params:
        return ""
    return urlencode(params, safe="_-.~")


def signature_payload(
    method: str,
    path: str,
    params: dict[str, str] | None,
    body: str,
    timestamp: int,
) -> str:
    """서명할 문자열을 만든다.

    Args:
        method: HTTP 메서드. 대문자로 올린다.
        path: `/futures/usdt/accounts` 처럼 **접두 없는** 경로. 여기서 `/api/v4` 를 붙인다.
        params: 쿼리 파라미터.
        body: 요청 본문. 없으면 빈 문자열.
        timestamp: **초** 단위 유닉스 시각.

    Returns:
        줄바꿈으로 이은 서명 문자열.

    Note:
        경로에 접두를 **여기서** 붙인다. 호출부가 붙이게 하면 어떤 곳은 붙이고 어떤
        곳은 안 붙이는 상태가 생기고, 그 차이가 401 로만 드러난다.
    """
    full = path if path.startswith(API_PREFIX) else f"{API_PREFIX}{path}"
    return "\n".join([method.upper(), full, query_string(params), body_hash(body), str(timestamp)])


def sign(secret: str, payload: str) -> str:
    """서명 문자열을 HMAC-SHA512 로 서명한다.

    Args:
        secret: API 시크릿.
        payload: `signature_payload` 결과.

    Returns:
        hex 소문자 서명.

    Raises:
        ValueError: 시크릿이 빈 경우. **조용히 서명하지 않는다** — 빈 시크릿으로
            만든 서명은 형태가 멀쩡해서 401 이 날 때까지 알 수 없다 (절대 규칙 #8).
    """
    if not secret:
        raise ValueError("Gate API 시크릿이 비었다 — 빈 시크릿으로 만든 서명은 형태가 멀쩡하다")
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha512).hexdigest()


def auth_headers(
    key: str,
    secret: str,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    body: str = "",
    timestamp: int,
) -> dict[str, str]:
    """인증 헤더 세 개.

    Args:
        key: API 키.
        secret: API 시크릿.
        method: HTTP 메서드.
        path: 접두 없는 경로.
        params: 쿼리 파라미터 — **실제 URL 에 붙는 순서대로**.
        body: 요청 본문.
        timestamp: 초 단위 유닉스 시각. **주입받는다** — 결정론 테스트가 가능해야 한다
            (절대 규칙 #5: 서명 계산에 현재시각을 직접 참조하지 않는다).

    Returns:
        `KEY` · `Timestamp` · `SIGN`.

    Raises:
        ValueError: 키나 시크릿이 빈 경우.
    """
    if not key:
        raise ValueError("Gate API 키가 비었다")
    payload = signature_payload(method, path, params, body, timestamp)
    return {
        "KEY": key,
        "Timestamp": str(timestamp),
        "SIGN": sign(secret, payload),
        "Content-Type": "application/json",
    }
