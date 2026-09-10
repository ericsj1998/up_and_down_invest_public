"""Gate.io v4 **서명** 클라이언트 — 잔고·주문·포지션·레버리지.

## 🔴 조회 클라이언트와 왜 나누는가

`GateClient` 는 자격증명을 **아예 받지 않는다.** 그것이 안전 속성이다 — 조회 경로에
버그가 있어도 주문을 낼 물리적 수단이 없다. 여기에 서명을 얹으면 그 속성이 사라진다.

⛔ 그래서 별도 클래스다. 이 파일을 import 하는 것 자체가 "키를 쓴다"는 선언이다.

## 절대 규칙 #6 — 조회가 주문보다 먼저다

> 모든 주문에 멱등키. **재시도 전 반드시 체결 여부를 먼저 조회한다.**

그래서 `find_order` 를 먼저 만들었다. 조회 없이 재시도하면 중복 주문이 나고, 그건
페이크머니에서는 웃기는 일이지만 라이브에서는 포지션이 두 배가 되는 일이다.

## 멱등키는 `text` 필드다

Gate 는 주문에 `text` 를 붙일 수 있고 `t-` 로 시작해야 한다. 우리 `trace_id` 를 여기
싣고, 재시도 전에 **그 text 로 조회**해서 이미 들어갔는지 본다.

✅ **멱등키로 조회가 된다** (실측 2026-08-17 · testnet). `find_order("t-...")` 가 주문을
   돌려준다 — 재시도 때 주문 id 를 몰라도 확인할 수 있다는 뜻이고, 규칙 #6 이 실제로
   지켜진다. 주문 id 를 따로 보관할 필요가 없다.

⚠️ **Gate 가 같은 `text` 를 거부하는지는 아직 모른다.** 거부한다면 멱등이 서버에서도
   보장되고, 안 한다면 우리 조회가 유일한 방어선이다. 확인되지 않은 것을 보장으로 적지
   않으므로 `find_order` 를 **항상** 거치는 구조를 유지한다 (절대 규칙 #8).
"""

import hashlib
import json
import time
from decimal import Decimal
from types import TracebackType
from typing import Any, Final, Self, cast

import httpx

from updown.common.http.outbound import NO_RETRY, Outbound, OutboundError
from updown.common.logging.setup import get_logger
from updown.marketdata.gate.client import (
    LIVE_BASE_URL,
    TESTNET_BASE_URL,
    GateApiError,
)
from updown.marketdata.gate.signing import auth_headers
from updown.marketdata.ratelimit import note_ban, observe
from updown.marketdata.speccache import spec as cached_spec

SETTLE: Final = "usdt"
IDEMPOTENCY_PREFIX: Final = "t-"
"""주문 `text` 접두 — Gate 규격이다. 없으면 400 이다."""

_HTTP_OK: Final = 200
_HTTP_CREATED: Final = 201
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_TOO_MANY = 429
"""요율 제한 — 밴이면 만료 시각을 남긴다 (밴 중에 부르면 연장된다)."""
_HTTP_NOT_FOUND: Final = 404

_logger = get_logger("marketdata.gate.trade")

STOP_EXPIRATION_S = 2_592_000
"""브로커측 조건부(스탑) 주문 유효기간 = 30일 (초).

2026-08-25 testnet 실측 수용값 — 24시간이면 서버가 하루 죽는 순간 포지션이 무방비가
된다. 만기 전 러너 재무장이 손절을 다시 건다.
"""

STOP_REFRESH_BEFORE_S = 604_800
"""만료가 이만큼(7일) 안으로 다가오면 손절을 **미리 갱신**한다 (장투 · 리뷰 갭2 · 2026-09-01).

🔴 **왜 필요한가**: `stops_for` 는 같은 가격 손절이 있으면 안 다시 걸어(만료 연장 안 함),
30일 만료 시계가 그대로 흘렀다 — 장투로 한 자리에 30일 넘게 있으면 day 30 에 손절이
조용히 만료되고, 다음 점검이 알아채 다시 걸 때까지(최대 점검 주기) 무방비였다. **앱이
돌아도** 생기는 창이다. 만료 7일 전에 미리 갈아 끼우면 도는 동안 손절이 끊기지 않는다.

⚠️ **7일인 이유**: 손절 수명이 늘 [7, 30]일 사이로 유지된다 = 앱이 죽어도 **최소 7일**은
브로커 손절이 버틴다(다운 버퍼). 3일이면 하필 갱신 직전에 죽으면 버퍼가 3일뿐이다.
갱신은 ~23일마다라 취소+재생성의 짧은 무방비 창이 드물다 — 버퍼와 그 창을 저울질한 값.

⚠️ 앱이 **7일 넘게** 죽어 있으면 결국 만료된다 — 그건 운영(앱을 살려 둔다 · 자동 재시작)의
몫이다. 코드로는 죽은 앱이 손절을 다시 걸 수 없다.
"""


def price_text(value: Decimal) -> str:
    """거래소로 나가는 **가격 문자열** — 꼬리 0 을 턴다.

    Args:
        value: 가격.

    Returns:
        `64524.5` 처럼 사람이 쓰는 모양.

    Note:
        🔴 **이것 때문에 손절이 안 걸렸다** (2026-08-19 실측). 원장의 `Decimal` 은
        연산을 거치며 소수점이 18자리까지 늘어나고, `str()` 이 그것을 그대로 보낸다:

        ```
        보낸 값   "64524.500000000000000000"
        응답      400 AUTO_INVALID_PARAM_TRIGGER_PRICE
        ```

        ⇒ **3배 레버리지 숏이 손절 없이 굴렀고**, 재시작 때 이어받기가 (손절이 없어서)
        거부하는 바람에 같은 자리에 한 번 더 진입해 **포지션이 두 배**가 됐다.

        ⚠️ 익절 지정가도 같은 경로다 — 코드 주석에 *"밤새 호가 자릿수 거절"* 이 남아
        있었는데 원인을 못 찾고 있었다. 이것이 그 원인이다.

        ⛔ **반올림이 아니다.** 값을 바꾸지 않고 표기만 정리한다 — 호가 눈금에 맞추는
        것은 계획을 세울 때 이미 했다 (`resolve_tick`). 여기서 또 반올림하면 어느 쪽이
        진짜인지 알 수 없게 된다.

        `normalize()` 는 `100.000` 을 `1E+2` 로 만들므로 `format(…, "f")` 로 편다.
    """
    return format(value.normalize(), "f")


TEXT_LIMIT: Final = 30
"""Gate `text` 필드의 길이 상한 (실측 2026-08-18).

🔴 넘기면 `400 INVALID_PARAM_VALUE — "text content length longer than 30"` 이다.
접두 `t-` 를 **포함한** 길이다.

⚠️ 문서에 없어서 실제로 부딛혀 알았다 — 거래소 콘솔의 청산 키가 34자였고 500 이 났다.
"""


def gate_text(key: str) -> str:
    """도메인 멱등키 → Gate `text` 필드.

    Args:
        key: 우리 멱등키 (`{root}:{order_kind}:{leg_index}` · spec §4.10).

    Returns:
        `t-` 로 시작하고 Gate 가 받는 문자만 남은 문자열.

    Raises:
        ValueError: 키가 빈 경우.

    Note:
        🔴 **Gate 는 콜론을 거부한다** (실측 2026-08-17):

            400 INVALID_PARAM_VALUE — "text content includes illegal characters"

        그런데 멱등키 규격은 `{root}:{order_kind}:{leg_index}` 다 (§4.10 · §9).
        규격을 바꾸지 않는다 — 콜론은 **주문 단위를 가르는 구분자**이고, 도메인이
        브로커 제약에 맞춰 휘면 다른 브로커를 붙일 때 또 휘어야 한다.

        ⇒ 여기서 **전송용으로만** 바꾼다. 콜론·그 외 허용 밖 문자를 `-` 로 옮긴다.

        ⚠️ **변환이 결정론이어야 한다.** `find_order` 가 같은 변환을 거쳐야 재시도 전
        조회(절대 규칙 #6)가 성립한다 — 다르게 변환하면 "이미 들어간 주문" 을 못 찾고
        중복 주문이 난다.

        🔴 **Gate 는 30자를 넘기면 거부한다** (실측 2026-08-18):

            400 INVALID_PARAM_VALUE — "text content length longer than 30"

        ⛔ **잘라내지 않는다.** 조용히 자르면 서로 다른 두 주문이 같은 text 를 갖고,
        그것이 멱등의 반대다. 대신 **해시로 접는다** — 결정론이고 충돌하지 않으므로
        `find_order` 의 재시도 조회가 그대로 성립한다 (절대 규칙 #6).

        ⚠️ 접힌 키는 사람이 읽을 수 없다. 그래서 **접기 전에 짧게 만드는 것이 우선**이고,
        접힘이 잦아지면 키 규격을 줄여야 한다는 신호다.
    """
    if not key:
        raise ValueError("멱등키가 비었다 — 재시도 때 같은 주문인지 알 수 없다")
    body = "".join(
        item if (item.isalnum() and item.isascii()) or item in "-_." else "-" for item in key
    )
    if not body.startswith(IDEMPOTENCY_PREFIX):
        body = f"{IDEMPOTENCY_PREFIX}{body}"
    if len(body) <= TEXT_LIMIT:
        return body
    # ⭐ 접기: 접두 + 원본 키의 해시. 원본이 같으면 결과도 같다.
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=8).hexdigest()
    return f"{IDEMPOTENCY_PREFIX}{digest}"


class GateAuthError(GateApiError):
    """401 — 서명·키·IP 중 하나가 틀렸다.

    Note:
        🔴 **셋을 구별할 수 없는 것이 이 예외의 요점이다.** Gate 는 세 경우에 모두 401 을
        준다. 그래서 메시지에 **지금 나가는 IP** 를 실어, 화이트리스트 불일치인지
        아닌지를 사람이 한 줄로 가릴 수 있게 한다.

        서명은 문서 정답 벡터로 잠겨 있으므로(`tests/test_gate_signing.py`) 용의자에서
        빠진다 — 남는 것은 키와 IP 다.
    """


class GateTradeClient:
    """서명이 필요한 Gate v4 엔드포인트.

    Note:
        ⚠️ **어디에 붙었는지 늘 드러낸다** (`is_testnet`). testnet 인 줄 알고 라이브에
        주문을 내는 것이 이 프로젝트에서 가장 비싼 사고가 될 수 있다.
    """

    def __init__(
        self,
        key: str,
        secret: str,
        *,
        base_url: str = TESTNET_BASE_URL,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            key: API 키.
            secret: API 시크릿.
            base_url: REST 베이스. **기본값이 testnet 이다** — 라이브는 부르는 쪽이
                명시해야 한다. 기본값을 라이브로 두면 실수의 방향이 돈이 나가는 쪽이다.
            timeout: 요청 타임아웃(초).
            transport: 테스트용 전송 계층 주입.

        Raises:
            ValueError: 키나 시크릿이 빈 경우 — 서명 단계까지 미루지 않고 여기서 막는다.
        """
        if not key or not secret:
            raise ValueError(
                "Gate 자격증명이 비었다 — 빈 시크릿으로 만든 서명은 형태가 멀쩡해서 "
                "401 이 날 때까지 알 수 없다"
            )
        self._key = key
        self._secret = secret
        self._base_url = base_url
        # 🔴 `NO_RETRY` — 주문은 층이 재시도하지 않는다 (규칙 #6: 재시도 전 체결 조회 먼저).
        #    닫힌 풀 재개방·타임아웃·호출 로그만 층(T264 3차). 서명·본문은 여기서 만든다.
        self._client = Outbound(
            "GATE",
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            policy=NO_RETRY,
            transport=transport,
        )

    @property
    def is_testnet(self) -> bool:
        """테스트넷에 붙어 있는가."""
        return self._base_url == TESTNET_BASE_URL

    @property
    def is_live(self) -> bool:
        """🔴 라이브에 붙어 있는가 — **진짜 돈이 움직이는 곳**이다."""
        return self._base_url == LIVE_BASE_URL

    async def __aenter__(self) -> Self:
        """컨텍스트 진입."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """컨텍스트 종료."""
        await self.aclose()

    async def aclose(self) -> None:
        """연결을 닫는다."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> object:
        """서명해서 보낸다.

        Args:
            method: HTTP 메서드.
            path: 접두 없는 경로 (`/futures/usdt/accounts`).
            params: 쿼리 — **URL 에 붙는 순서대로**.
            body: JSON 본문.

        Returns:
            파싱된 JSON.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외 실패.

        Note:
            🔴 **재시도하지 않는다.** 조회는 재시도해도 되지만 주문은 아니고, 한 함수가
            둘 다 하면 언젠가 주문이 재시도된다. 재시도는 **부르는 쪽이 절대 규칙 #6
            (조회 먼저)을 지키며** 해야 한다.

            본문 직렬화는 서명과 전송이 **같은 문자열**을 써야 한다. httpx 의 `json=`
            을 쓰면 httpx 가 자기 방식으로 다시 직렬화해 공백 하나가 달라질 수 있고,
            그러면 서명이 깨진다 — 그래서 우리가 만든 문자열을 `content=` 로 보낸다.
        """
        text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        stamp = int(time.time())
        headers = auth_headers(
            self._key, self._secret, method, path, params=params, body=text, timestamp=stamp
        )
        try:
            response = await self._client.request(
                method, path, params=params, content=text or None, headers=headers
            )
        except OutboundError as exc:
            # 전송 실패 — 보냈는지 모른다. 재시도는 부르는 쪽이 체결 조회 뒤에(규칙 #6).
            raise GateApiError(
                f"Gate 전송 실패({method} {path}): {exc}", status_code=None, payload=""
            ) from exc
        # 🔴 **얼마나 썼는지 헤더로 안다** (2026-08-29 사고 · Binance 와 같은 사정).
        #    Gate 는 `x-gate-ratelimit-remain` 으로 **남은 것**을 준다. 실패 응답에도
        #    실리므로 성공 판정보다 먼저 읽는다.
        observe("GATE", response.headers)
        if response.status_code == _HTTP_TOO_MANY:
            note_ban("GATE", response.text[:300])
        if response.status_code in (_HTTP_OK, _HTTP_CREATED):
            return response.json()
        if response.status_code == _HTTP_UNAUTHORIZED:
            raise GateAuthError(
                f"Gate 401 ({path}) — 서명·키·IP 중 하나다. 서명은 문서 정답 벡터로 "
                f"잠겨 있으니 키나 IP 화이트리스트를 본다. 나가는 IP 는 "
                f"`curl -s https://api.ipify.org` 로 확인한다. 응답={response.text[:200]}",
                status_code=response.status_code,
                payload=response.text,
            )
        raise GateApiError(
            f"Gate 오류 응답({method} {path}): {response.status_code} {response.text[:300]}",
            status_code=response.status_code,
            payload=response.text,
        )

    # ── 조회 (주문보다 먼저다 · 절대 규칙 #6) ────────────────────────

    async def get_account(self) -> dict[str, Any]:
        """선물 계좌 잔고.

        Returns:
            `total`·`available`·`unrealised_pnl` 등.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.
        """
        found = await self._request("GET", f"/futures/{SETTLE}/accounts")
        if not isinstance(found, dict):
            raise GateApiError(f"계좌 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    async def account_book(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """**자금 변동 원장** — 돈이 왜 움직였는지 (T14-2).

        Args:
            limit: 가져올 줄 수.

        Returns:
            최근 변동들. 각 줄에 `type` · `change` · `time` · `text` 가 있다.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **청산과 우리 손절 체결을 가르는 유일한 근거다.** "포지션이 사라졌다" 만
            보면 둘이 똑같이 보이고, 구별하지 못하면 정상 손절을 청산으로 세어 성적이
            망가진다 — 청산은 `gain_pct` 가 **-100%** 로 기록되는 사건이다.

            Gate 의 `type` 값 (2026-08-19 문서 기준):

            ```
            dnw   입출금        pnl   포지션 손익(정상 청산 포함)
            fee   거래 수수료    refr  리베이트
            fund  펀딩비        point_* 포인트 계열
            ```

            ⚠️ **청산 전용 type 이 따로 없다.** 강제청산도 `pnl` 로 들어오고, 구별은
            `text` 에 남는다 — 그래서 부르는 쪽이 문자열을 본다. 여기서는 **읽어 주기만**
            한다 (해석을 클라이언트에 넣으면 거래소 문구가 바뀔 때 여기가 깨진다).
        """
        found = await self._request(
            "GET", f"/futures/{SETTLE}/account_book", params={"limit": str(limit)}
        )
        if not isinstance(found, list):
            raise GateApiError(f"자금 원장 응답이 배열이 아니다: {type(found).__name__}")
        return cast("list[dict[str, Any]]", found)

    async def position_closes(self, contract: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """**닫힌 포지션의 실현 손익** (사용자 요구 2026-08-19).

        Args:
            contract: 계약 이름.
            limit: 가져올 줄 수.

        Returns:
            최근 청산들. `pnl` · `side` · `time` · `text` 를 담는다.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **주문 이력에는 손익이 없다** (2026-08-19 실측). 체결 행이 주는 것은
            `fill_price` 까지이고, *"그래서 얼마 벌었나"* 는 여기에만 있다.

            ⚠️ **주문 하나가 아니라 포지션 하나의 손익이다.** 분할 청산이면 여러 체결이
            한 줄로 합쳐진다 — 체결 줄에 그대로 붙이면 같은 손익이 여러 번 세어진다.
        """
        found = await self._request(
            "GET",
            f"/futures/{SETTLE}/position_close",
            params={"contract": contract, "limit": str(limit)},
        )
        if not isinstance(found, list):
            raise GateApiError(f"청산 이력이 배열이 아니다: {type(found).__name__}")
        return cast("list[dict[str, Any]]", found)

    async def contract(self, contract: str) -> dict[str, Any]:
        """**주문이 나가는 곳**의 계약 명세 (2026-08-19 사고).

        Args:
            contract: 계약 이름.

        Returns:
            `order_price_round` · `quanto_multiplier` 등.

        Raises:
            GateApiError: 응답이 객체가 아닌 경우.

        Note:
            🔴 **명세를 라이브에서 읽고 주문을 testnet 에 내고 있었다.** 두 곳의 호가
            단위가 다르면 우리가 만든 가격이 **전부 무효**다 — 실측:

            ```
            보낸 값  1919.02 (ETH · 라이브 단위 0.01 의 배수)
            응답     400 "trigger.price price is not an integer multiple of a price unit"
            ```

            ⇒ **20배 숏이 손절 없이 굴렀고**, 안전장치가 세 번 실패한 뒤 시장가로 던졌다.

            ⚠️ 이미 알던 부류의 차이다 — testnet 은 `maintenance_rate`·`leverage_max` 도
            라이브와 다르다. 그때 "하드코딩하지 않고 API 에서 읽는다" 고 적어 뒀는데,
            **어느 API 인지**는 안 적혀 있었다.

            ⭐ **한 시간 기억한다** (`speccache`). 실패는 기억하지 않으므로 요율 제한
            한 번이 한 시간짜리 공백이 되지는 않는다.
        """

        async def ask() -> dict[str, Any]:
            """거래소에 실제로 묻는다 — 기억통이 비었을 때만 불린다.

            Returns:
                계약 명세 원문.

            Raises:
                GateApiError: 응답이 객체가 아니다.
            """
            found = await self._request("GET", f"/futures/{SETTLE}/contracts/{contract}")
            if not isinstance(found, dict):
                raise GateApiError(f"계약 명세가 객체가 아니다: {type(found).__name__}")
            return cast("dict[str, Any]", found)

        # 🔴 **기억통을 거친다** (2026-08-29 · Binance 와 같은 사정). 호가 단위·승수는
        #    상장 규칙이 바뀔 때나 변하는 값인데 걸음마다 물었다. 그 낭비가 요율 한도를
        #    갉아먹는다 — Binance 쪽에서 실제로 IP 밴까지 갔다.
        return await cached_spec("GATE", contract, ask)

    async def get_identity(self) -> dict[str, Any]:
        """이 키가 **누구의 계정인지** — 화면이 표시할 식별자.

        Returns:
            `/account/detail` 응답. `user_id` · `ip_whitelist` · `tier` 를 담는다.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **어느 계정에 주문이 나가는지 화면이 말해야 한다** (사용자 요구 2026-08-18).
            키를 바꿔 끼운 것을 모르면, 다른 계정의 잔고를 보며 내 성적이라고 읽는다.

            ⚠️ **계정 이름(닉네임)은 Gate 가 주지 않는다** — `user_id` 뿐이다. 없는 값을
            화면에 만들지 않는다 (절대 규칙 #8).

            ⭐ `ip_whitelist` 를 함께 낸다. 401 이 났을 때 *"IP 가 바뀐 것"* 을 가장 먼저
            의심해야 하고, 그 값이 화면에 있으면 즉시 확인된다.
        """
        found = await self._request("GET", "/account/detail")
        return cast("dict[str, Any]", found)

    async def get_positions(self) -> list[dict[str, Any]]:
        """보유 포지션들.

        Returns:
            포지션 목록. 없으면 빈 리스트.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            ⚠️ **`size` 가 0 인 포지션도 온다** (계약 설정만 있는 상태). 보유로 세면
            없는 포지션을 관리하려 든다 — 부르는 쪽이 `size != 0` 을 본다.
        """
        found = await self._request("GET", f"/futures/{SETTLE}/positions")
        if not isinstance(found, list):
            raise GateApiError(f"포지션 응답이 배열이 아니다: {type(found).__name__}")
        return cast("list[dict[str, Any]]", found)

    async def find_order(self, order_id: str) -> dict[str, Any] | None:
        """주문 하나를 조회한다 — **재시도 전에 반드시 부른다** (절대 규칙 #6).

        Args:
            order_id: Gate 주문 id, 또는 `t-` 로 시작하는 우리 멱등키.

        Returns:
            주문. 없으면 None.

        Raises:
            GateAuthError: 401.
            GateApiError: 404 를 제외한 실패.

        Note:
            🔴 **404 를 None 으로 접는다.** "주문이 없다"는 오류가 아니라 답이다 —
            재시도해도 되는 상태다. 반대로 다른 실패를 None 으로 접으면 "없으니 다시
            낸다"가 되어 중복 주문이 나므로, 그것만은 던진다.

            ⚠️ 멱등키(`t-...`)로 조회할 수 있는지는 **확인하지 않았다.** 되면 재시도가
            안전해지고, 안 되면 주문 id 를 우리가 보관해야 한다 — 확인 전에는 보관하는
            쪽으로 짠다.
        """
        # 🔴 **같은 변환을 거친다.** 우리 멱등키(콜론 포함)를 그대로 물으면 Gate 에는
        #    그런 text 가 없다 — 못 찾고 "안 들어갔다" 로 읽어 중복 주문이 난다.
        wanted = gate_text(order_id) if ":" in order_id else order_id
        try:
            found = await self._request("GET", f"/futures/{SETTLE}/orders/{wanted}")
        except GateApiError as exc:
            if exc.status_code == _HTTP_NOT_FOUND:
                return None
            raise
        if not isinstance(found, dict):
            raise GateApiError(f"주문 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    async def list_orders(self, contract: str, status: str = "open") -> list[dict[str, Any]]:
        """주문 목록.

        Args:
            contract: `BTC_USDT`.
            status: `open` 또는 `finished`.

        Returns:
            주문 목록.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.
        """
        found = await self._request(
            "GET",
            f"/futures/{SETTLE}/orders",
            params={"contract": contract, "status": status},
        )
        if not isinstance(found, list):
            raise GateApiError(f"주문 목록이 배열이 아니다: {type(found).__name__}")
        return cast("list[dict[str, Any]]", found)

    # ── 설정 ─────────────────────────────────────────────────────────

    async def set_leverage(self, contract: str, leverage: Decimal) -> dict[str, Any]:
        """레버리지를 정한다.

        Args:
            contract: `BTC_USDT`.
            leverage: 배율. **`0` 은 크로스 마진**이라는 뜻이므로 격리를 원하면 1 이상.

        Returns:
            갱신된 포지션.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **`0` 을 실수로 넣으면 크로스 마진이 된다.** 격리 마진은 그 포지션의
            증거금만 잃지만 크로스는 계좌 전체가 담보다 — 우리 청산가 계산이 격리
            기준(`ledger.liquidation_price`)이라, 크로스로 돌면 계산이 뜻을 잃는다.
            그래서 0 을 막는다.

            🔴 **유지증거금률은 계약 명세가 정답이다** (실측 2026-08-17 · testnet).
            두 엔드포인트가 다른 값을 주는데:

                /futures/usdt/contracts/BTC_USDT  →  maintenance_rate 0.004
                /futures/usdt/positions/...       →  maintenance_rate 0.3

            Gate 가 계산한 `liq_price` 에서 역산해 갈랐다. 청산을 실제로 일으키지
            않았다 — 가격이 움직여야 해서 시간이 걸리고 얻는 정보는 같다.

                liq = entry x (1 - 1/L) / (1 - mmr)
                ⇒ mmr = 1 - entry x (1 - 1/L) / liq

                진입 63661 · 청산가 42595.25 · 레버리지 3  →  mmr 0.003629
                계약 명세 0.004 와의 차이 0.000371
                테이커 수수료의 절반      0.000375   ← 거의 정확히 일치

            차이가 **수수료 완충으로 설명된다.** 그래서 명세 값을 쓴다.

            ⚠️ 처음에는 `liq ≈ entry x (1 - 1/L + mmr)` 로 풀어 0.002428 이 나왔고 세
              값 어디에도 안 맞았다 — **근사식의 오차였다.** 증거금을 제대로 이항해야 한다.

            ⛔ **포지션 응답의 `maintenance_rate`(0.3)는 쓰지 않는다.** 0.3% 로 읽어도
              (0.003) 명세와 안 맞는다. 구간별 값이거나 표시용이고, 어느 쪽이든 근거로
              쓸 수 없다.

            ⭐ 원장 상수 0.005(`ledger.MAINTENANCE_MARGIN`)는 실제보다 **크다** →
              청산선을 실제보다 **가깝게** 계산한다 → 보수적이고 안전한 방향의 오차다.
        """
        if leverage <= 0:
            raise ValueError(
                f"레버리지가 {leverage} 다 — Gate 에서 0 은 **크로스 마진**이고, 우리 청산가 "
                "계산은 격리 기준이라 뜻이 달라진다. 격리를 원하면 1 이상을 준다"
            )
        found = await self._request(
            "POST",
            f"/futures/{SETTLE}/positions/{contract}/leverage",
            params={"leverage": str(leverage)},
        )
        if not isinstance(found, dict):
            raise GateApiError(f"레버리지 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    # ── 주문 ─────────────────────────────────────────────────────────

    async def place_order(
        self,
        contract: str,
        size: int,
        *,
        idempotency_key: str,
        price: Decimal | None = None,
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> dict[str, Any]:
        """주문을 낸다.

        Args:
            contract: `BTC_USDT`.
            size: **부호 있는 계약 수.** 양수가 롱, 음수가 숏이다. 0 은 거부한다.
            idempotency_key: 우리 멱등키. `t-` 접두는 여기서 붙인다.
            price: 지정가. None 이면 **시장가**(`price="0"` + `tif="ioc"`)다.
            reduce_only: 포지션을 줄이는 주문인가. 익절·손절은 참이어야 한다.
            post_only: 참이면 지정가를 `tif="poc"` 로 — **즉시 체결될 상황이면 Gate 가
                거부**한다(재가격 아님). 메이커 요율 보장 (T60 축④). 시장가에는 무의미.

        Returns:
            생성된 주문.

        Raises:
            ValueError: `size` 가 0 이거나 멱등키가 빈 경우.
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **방향이 `size` 의 부호다.** `side` 필드가 없다 — 업비트·토스와 다르므로
            상위 계층의 `Side` 를 여기서 부호로 옮긴다. 부호를 잘못 주면 **반대 방향
            포지션**이 열리고, 그것은 손절이 이익 방향에 놓인다는 뜻이다.

            🔴 **시장가는 `price="0"` + `tif="ioc"`** 다. `type` 필드로 구분하지 않는다.
            `tif` 를 빼면 지정가 0 원 주문으로 해석돼 거부된다.

            ⚠️ **`size` 는 계약 수다** (1계약 = 0.0001 BTC). 수량을 BTC 로 넘기면
            1만분의 1 만 사게 된다 — 조용히 성공하는 종류의 실수다.

            ⚠️ `reduce_only` 를 빼면 청산 주문이 **반대 포지션을 새로 연다.** 그러면
            원래 포지션이 그대로 남고 위험이 두 배가 된다.
        """
        if size == 0:
            raise ValueError("size 가 0 이다 — 방향이 없는 주문은 뜻이 없다")
        if not idempotency_key:
            raise ValueError("멱등키가 비었다 — 재시도 때 같은 주문인지 알 수 없다 (절대 규칙 #6)")
        text = gate_text(idempotency_key)
        body: dict[str, Any] = {
            "contract": contract,
            "size": size,
            "text": text,
            "reduce_only": reduce_only,
        }
        if price is None:
            # 시장가 — 가격 0 과 IOC 를 함께 준다. 하나만 주면 거부된다.
            body["price"] = "0"
            body["tif"] = "ioc"
        else:
            # 🔴 꼬리 0 을 털어 보낸다 — 소수점 18자리가 400 을 부른다.
            body["price"] = price_text(price)
            # ⭐ poc = post-only — 크로스면 거부돼 테이커 체결이 원천 차단된다 (T60 축④).
            body["tif"] = "poc" if post_only else "gtc"
        _logger.info(
            "gate_order_submit",
            payload={
                "contract": contract,
                "size": size,
                "market": price is None,
                "reduce_only": reduce_only,
                "post_only": post_only,
                "text": text,
                "testnet": self.is_testnet,
            },
        )
        found = await self._request("POST", f"/futures/{SETTLE}/orders", body=body)
        if not isinstance(found, dict):
            raise GateApiError(f"주문 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    async def close_position(self, contract: str, *, idempotency_key: str) -> dict[str, Any]:
        """포지션을 전량 청산한다 (시장가).

        Args:
            contract: `BTC_USDT`.
            idempotency_key: 멱등키.

        Returns:
            생성된 주문.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **`close=true` + `size=0`** 이 Gate 의 전량 청산 규격이다. 수량을 우리가
            세어 넣으면 부분 체결·펀딩으로 크기가 달라진 만큼 남거나 넘친다 —
            거래소가 세는 것이 정확하다.

            ⛔ `reduce_only` 와 함께 보내면 400 이다 (`close` 가 이미 그 뜻이다).
        """
        body: dict[str, Any] = {
            "contract": contract,
            "size": 0,
            "close": True,
            "price": "0",
            "tif": "ioc",
            "text": gate_text(idempotency_key),
        }
        _logger.info(
            "gate_position_close",
            payload={"contract": contract, "testnet": self.is_testnet},
        )
        found = await self._request("POST", f"/futures/{SETTLE}/orders", body=body)
        if not isinstance(found, dict):
            raise GateApiError(f"청산 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """주문을 취소한다.

        Args:
            order_id: Gate 주문 id.

        Returns:
            취소된 주문.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            ⚠️ 이미 체결된 주문 취소는 실패한다 — 그것이 정상이다. 실패를 성공으로
            접으면 "취소했다" 고 믿는 포지션이 살아 있게 된다 (절대 규칙 #8).
        """
        found = await self._request("DELETE", f"/futures/{SETTLE}/orders/{order_id}")
        if not isinstance(found, dict):
            raise GateApiError(f"취소 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    async def get_position(self, contract: str) -> dict[str, Any]:
        """포지션 하나 — **청산가를 여기서 읽는다**.

        Args:
            contract: `BTC_USDT`.

        Returns:
            포지션. 보유가 없어도 계약 설정이 온다 (`size: 0`).

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **거래소가 계산한 `liq_price` 가 우리 계산의 정답지다.** 유지증거금률이
            두 값으로 와서(0.004 vs 0.3) 어느 쪽인지 모르는데, 실제 포지션의 청산가에서
            **역산**하면 알 수 있다:

                격리 롱:  liq ≈ entry x (1 - 1/L + mmr)
                ⇒ mmr = liq/entry - 1 + 1/L

            청산을 실제로 일으킬 필요가 없다. 그건 시간이 걸리고 얻는 것도 같다.
        """
        found = await self._request("GET", f"/futures/{SETTLE}/positions/{contract}")
        if not isinstance(found, dict):
            raise GateApiError(f"포지션 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    # ── 브로커측 조건부 주문 (실측 2026-08-17) ───────────────────────

    async def place_stop(
        self,
        contract: str,
        trigger: Decimal,
        *,
        long: bool,
        price_type: int = 0,
        expiration: int = STOP_EXPIRATION_S,
    ) -> str:
        """브로커에 **조건부 손절**을 건다 — 서버가 죽어도 발동한다.

        Args:
            contract: `BTC_USDT`.
            trigger: 발동 가격.
            long: 보유가 롱인가. 방향이 `rule` 과 `auto_size` 를 정한다.
            price_type: 0 최종가 · 1 마크가 · 2 인덱스가.
            expiration: 유효 기간(초). 기본 `STOP_EXPIRATION_S`(30일) — 근거는 상수
                docstring 에 있다.

        Returns:
            조건부 주문 id.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **이것이 서버 다운 중 손절의 유일한 방어선이다** (spec §7 · §12.6).
            우리가 감시하는 손절은 프로세스가 살아 있어야 돈다 — 정전·재부팅·인터넷
            끊김에 포지션이 무방비로 남는다.

            🔴 **방향이 `rule` 을 뒤집는다** (실측):

                롱 손절  가격이 **내려가면** 발동  → rule 2 (<=) · close_long
                숏 손절  가격이 **올라가면** 발동  → rule 1 (>=) · close_short

            뒤집으면 손절이 **이익 방향에** 걸리고, 그러면 이익이 날 때마다 청산된다.

            ⭐ **`auto_size` 로 전량 청산한다** (`size: 0`). 수량을 우리가 세면 부분
              체결·펀딩으로 크기가 달라진 만큼 남거나 넘친다 — 거래소가 세는 것이 정확하다.

            ⚠️ `price_type` 기본이 **최종가**다. 마크가(1)로 하면 청산 판정과 같은 기준이
              되지만, 최종가가 사용자가 차트에서 보는 값이다. 바꾸려면 축 후보로 올린다.

            ⚠️ **만료가 있다** (기본 30일 · 2026-08-25 에 24h→30일로 늘렸다). 만료되면
              조건부가 조용히 사라진다 — 러너가 매 걸음 다시 거는 것은 유지하되(값 갱신),
              서버가 죽어 있는 동안의 방어선이 하루에서 한 달로 늘었다.
        """
        payload: dict[str, Any] = {
            "initial": {
                "contract": contract,
                "size": 0,
                "price": "0",
                "tif": "ioc",
                "reduce_only": True,
                "auto_size": "close_long" if long else "close_short",
            },
            "trigger": {
                "strategy_type": 0,
                "price_type": price_type,
                # 🔴 꼬리 0 을 털어 보낸다 — 이것 때문에 손절이 안 걸렸다.
                "price": price_text(trigger),
                # 🔴 롱은 내려갈 때(2), 숏은 올라갈 때(1). 뒤집으면 이익에 손절이 걸린다.
                "rule": 2 if long else 1,
                "expiration": expiration,
            },
        }
        _logger.info(
            "gate_stop_placed",
            payload={
                "contract": contract,
                "trigger": price_text(trigger),
                "direction": "롱" if long else "숏",
                "rule": payload["trigger"]["rule"],
                "testnet": self.is_testnet,
            },
        )
        found = await self._request("POST", f"/futures/{SETTLE}/price_orders", body=payload)
        if not isinstance(found, dict):
            raise GateApiError(f"조건부 주문 응답이 객체가 아니다: {type(found).__name__}")
        return str(cast("dict[str, Any]", found).get("id", ""))

    async def order_book(self, contract: str, limit: int = 20) -> dict[str, Any]:
        """**주문이 나가는 곳**의 호가창 (2026-08-20 사고).

        Args:
            contract: `BTC_USDT`.
            limit: 단계 수.

        Returns:
            `{bids: [{p, s}], asks: [...]}` — 잔량 `s` 는 **계약 수**다.

        Raises:
            GateApiError: 응답 형식이 다르거나 호출 실패.

        Note:
            🔴 **조회 어댑터(라이브)가 아니라 여기서 읽어야 한다.** `contract_spec` 이
            같은 이유로 이미 그렇게 돼 있는데(2026-08-19 사고) 호가창은 안 고쳐져 있었다.

            실측 2026-08-20 · SPCX_USDT:

            ```
            라이브   매수 1호가가 표시가에서 0.01%   → "건강하다"
            testnet  매수 1호가가 표시가에서 22.3%   → 팔 곳이 없다
            ```

            우리 포지션은 **testnet 에** 있다. 라이브 호가로 판단하면 증거금 420 이 묶인
            바로 그 계약을 *"들어가도 좋다"* 고 답한다.

            ⚠️ **어느 거래소의 값인지가 곧 그 값의 뜻이다.**
        """
        found = await self._request(
            "GET",
            f"/futures/{SETTLE}/order_book",
            params={"contract": contract, "limit": str(limit)},
        )
        if not isinstance(found, dict):
            raise GateApiError(f"호가 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    async def list_stops(self, contract: str) -> list[dict[str, Any]]:
        """걸려 있는 조건부 주문들.

        Args:
            contract: `BTC_USDT`.

        Returns:
            열린 조건부 주문 목록.

        Raises:
            GateApiError: 응답이 배열이 아니다.

        Note:
            🔴 **포지션이 있으면 이 목록이 비어 있지 않아야 한다.** 비었는데 포지션이
            있으면 손절이 없는 상태다 — 만료됐거나 걸리지 않은 것이고, 둘 다 사건이다.
        """
        found = await self._request(
            "GET",
            f"/futures/{SETTLE}/price_orders",
            params={"status": "open", "contract": contract},
        )
        if not isinstance(found, list):
            raise GateApiError(f"조건부 목록이 배열이 아니다: {type(found).__name__}")
        return cast("list[dict[str, Any]]", found)

    async def cancel_stop(self, order_id: str) -> dict[str, Any]:
        """조건부 주문을 취소한다.

        Args:
            order_id: 조건부 주문 id.

        Returns:
            취소된 주문.

        Raises:
            GateApiError: 응답이 객체가 아니다.

        Note:
            ⚠️ 포지션을 닫은 뒤에는 **반드시 취소한다.** 남겨 두면 다음 포지션에서
            엉뚱한 가격에 발동하거나, `reduce_only` 라 조용히 실패한다.
        """
        found = await self._request("DELETE", f"/futures/{SETTLE}/price_orders/{order_id}")
        if not isinstance(found, dict):
            raise GateApiError(f"조건부 취소 응답이 객체가 아니다: {type(found).__name__}")
        return cast("dict[str, Any]", found)

    @staticmethod
    def fees_from(order: dict[str, Any]) -> tuple[Decimal, Decimal]:
        """주문 응답에서 **이 계정이 실제로 내는** 수수료율.

        Args:
            order: 주문 응답 (`place_order` 결과).

        Returns:
            `(테이커, 메이커)` 편도 비율.

        Raises:
            GateApiError: 필드가 없는 경우.

        Note:
            🔴 **계약 명세와 다르다** (실측 2026-08-17 · testnet):

                /contracts/BTC_USDT  →  taker 0.00075 · maker -0.0001   (기준 요율)
                주문 응답 tkfr/mkfr  →  taker 0.0005  · maker  0.0002   (이 계정)

            계약 명세는 **기준**이고 주문 응답이 **실제**다. VIP 등급·GT 차감·수수료
            이벤트가 계정별로 다르므로, 비용 판정은 반드시 이쪽을 봐야 한다.

            ⚠️ 메이커가 **양수**다. 명세의 -0.0001(리베이트)로 계산하면 청산 다리가
              돈을 받는 것으로 잡히는데 실제로는 낸다 — 낙관 방향의 오차다.
        """
        taker, maker = order.get("tkfr"), order.get("mkfr")
        if taker is None or maker is None:
            raise GateApiError(
                "주문 응답에 tkfr/mkfr 이 없다 — 계정 실제 수수료율을 알 수 없다. "
                "계약 명세 값으로 대체하면 낙관 방향으로 틀릴 수 있다"
            )
        return Decimal(str(taker)), Decimal(str(maker))
