"""바이낸스 USDT-M **서명** 클라이언트 — 주문·잔고·포지션 (T62 P2).

🔴 기본 베이스가 **testnet** 이다 (`testnet.binancefuture.com`) — Gate 와 같은 이중
잠금의 절반이다. 나머지 절반은 `BinancePaperAdapter` 생성자가 testnet 아닌 클라이언트를
거부하는 것 (`execution/binance_paper.py`).

## Gate 와 다른 점 — 알고 쓴다

```
방향      side=BUY/SELL 필드 (Gate 는 size 부호)
수량      코인 수량 + stepSize (Gate 는 정수 계약 x 승수) — 이 경계에서 변환한다
멱등키    newClientOrderId · **36자 제한** — 넘으면 잘라 보내며 그 사실을 로그로 남긴다
post-only timeInForce=GTX (Gate 는 tif=poc)
조건부    STOP_MARKET + closePosition=true — ⭐ GTC 라 **24시간 만료가 없다** (Gate 개선점)
주문조회  symbol 이 필수다 → 이 클라이언트가 {주문id→심볼} 을 기억한다 (아래 Note)
```
"""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from types import TracebackType
from typing import Any, Self, cast
from urllib.parse import urlencode

import httpx

from updown.common.logging.setup import get_logger
from updown.marketdata.binance.mapping import price_text, spec_with_compat
from updown.marketdata.ratelimit import note_ban, observe
from updown.marketdata.speccache import spec as cached_spec

_logger = get_logger("marketdata.binance.trade")

BINANCE_MINUTE_WEIGHT = 2400
"""1분 창의 요청 가중치 한도 — 헤더가 한도를 안 주므로 이 값으로 비율을 잰다.

⚠️ **거래소가 바꾸면 여기도 바꿔야 한다.** 다만 틀려도 위험하지 않다 — 이 값은
경보 문턱을 정할 뿐이고, 실제 소모량(`x-mbx-used-weight-1m`)은 거래소가 준 값 그대로다.
"""

TESTNET_BASE = "https://testnet.binancefuture.com"
LIVE_BASE = "https://fapi.binance.com"

#: newClientOrderId 규격 — 36자 초과는 거래소가 거부한다.
CLIENT_ID_LIMIT = 36

_ORDER = "/fapi/v1/order"
_OPEN_ORDERS = "/fapi/v1/openOrders"
_ALL_ORDERS = "/fapi/v1/allOrders"
_LEVERAGE = "/fapi/v1/leverage"
_INCOME = "/fapi/v1/income"
_ACCOUNT = "/fapi/v2/account"
_POSITION_RISK = "/fapi/v2/positionRisk"
_EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
_ALGO_ORDER = "/fapi/v1/algoOrder"
_OPEN_ALGO = "/fapi/v1/openAlgoOrders"
_DEPTH = "/fapi/v1/depth"

#: 조건부(브로커측 손절·익절) 주문 유형.
_STOP_TYPES = frozenset({"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"})


class BinanceTradeError(RuntimeError):
    """바이낸스 서명 호출 실패 — 코드·본문을 담는다 (규칙 #8)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """메시지와 상태코드를 담는다."""
        super().__init__(message)
        self.status_code = status_code


class BinanceAuthError(BinanceTradeError):
    """인증 실패 — 키·서명·IP 화이트리스트 문제다. 즉시 사람에게 (규칙 #8)."""


def clip_client_id(key: str) -> str:
    """멱등키를 newClientOrderId 규격(36자)에 맞춘다.

    Args:
        key: 우리 멱등키.

    Returns:
        36자 이하로 자른 키. 안 넘으면 그대로.

    Note:
        ⚠️ 자르면 멱등성 범위가 좁아진다 — 앞 36자가 같은 두 키는 같은 주문으로
        보인다. 우리 키는 앞부분이 trade_id 라 실질 충돌은 없지만, 잘랐다는 사실을
        로그에 남긴다 (조용히 바꾸지 않는다).
    """
    if len(key) <= CLIENT_ID_LIMIT:
        return key
    clipped = key[:CLIENT_ID_LIMIT]
    _logger.warning("binance_client_id_clipped", payload={"from": key, "to": clipped})
    return clipped


class BinanceTradeClient:
    """서명 REST — 잔고·포지션·주문·조건부·레버리지·수입 이력.

    Note:
        🔴 **주문 조회에 symbol 이 필수다** (Gate 는 id 만으로 됐다). 그래서 주문을
        낼 때마다 `{주문id·멱등키 → 심볼}` 을 기억해 두고, 취소·상태 조회가 그 기억으로
        심볼을 찾는다. 프로세스가 재시작하면 기억이 비므로 — 그때는 부르는 쪽이
        심볼 문맥에서 `find_order(key, symbol=...)` 로 직접 준다.
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str = TESTNET_BASE) -> None:
        """클라이언트를 만든다.

        Args:
            api_key: API 키.
            api_secret: 서명 비밀키.
            base_url: 기본 **testnet**. 라이브는 명시적으로 넘겨야 한다.
        """
        self._key = api_key
        self._secret = api_secret.encode()
        self._base_url = base_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None
        self._symbol_of: dict[str, str] = {}
        # 🔴 서버시간 오프셋(ms) — 이 호스트는 WSL↔Windows 재동기화가 시계를 계속
        #    +1.1s 로 끌고 간다(RTC 는 +2일 고장, 2026-08-25 실측). OS 를 고치는
        #    싸움 대신 서명 timestamp 를 서버 기준으로 만든다.
        self._clock_offset_ms: int | None = None

    @property
    def is_testnet(self) -> bool:
        """Testnet 인가."""
        return "testnet" in self._base_url

    @property
    def is_live(self) -> bool:
        """라이브 계정인가."""
        return not self.is_testnet

    async def __aenter__(self) -> Self:
        """컨텍스트 진입."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """컨텍스트 종료 — 연결을 닫는다."""
        await self.aclose()

    async def aclose(self) -> None:
        """연결을 닫는다."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _session(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._base_url, timeout=10.0)
        return self._http

    async def _server_offset(self, *, refresh: bool = False) -> int:
        """서버시간 - 로컬시간 (ms). 캐시하며, `refresh` 면 다시 잰다.

        Note:
            🔴 이 호스트의 시계는 못 믿는다 — WSL 이 주기적으로 Windows 시계(+1.1s)로
            되돌리고, RTC 는 +2일 틀려 있다 (2026-08-25 실측: chrony 로 맞춰도 수십 초
            만에 +1.1s 복귀). 바이낸스는 1000ms ahead 를 거절하므로(-1021) 서버 기준으로
            timestamp 를 만드는 것이 유일하게 호스트 상태와 무관한 길이다.

            ⚠️ 판정이 아니라 전송 계층이다 — 결정론 코어(절대 규칙 #5)는 봉 시각을 쓰고
            이 값은 서명 유효성에만 쓰인다.
        """
        if self._clock_offset_ms is None or refresh:
            try:
                response = await self._session().get("/fapi/v1/time")
                server = int(cast("dict[str, Any]", response.json())["serverTime"])
                self._clock_offset_ms = server - int(time.time() * 1000)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                # ⛔ 시간 조회 실패로 본 요청을 막지 않는다 — 오프셋 0 으로 시도하고,
                #   틀리면 -1021 재시도 경로가 다시 온다.
                _logger.warning("binance_time_unreadable", payload={"error": str(exc)[:120]})
                return self._clock_offset_ms or 0
        return self._clock_offset_ms

    async def _request(
        self, method: str, path: str, params: dict[str, str] | None = None
    ) -> list[Any] | dict[str, Any]:
        """서명 요청 — 쿼리에 timestamp 를 더하고 HMAC-SHA256 서명을 붙인다.

        Note:
            timestamp = 로컬시간 + 서버 오프셋. -1021(시계 어긋남)이 오면 오프셋을
            다시 재고 **한 번만** 재시도한다 — -1021 은 집행 전 거절이라 주문이 생기지
            않았으므로 재전송이 멱등 규칙(절대 규칙 #6)과 충돌하지 않는다.

        Raises:
            BinanceAuthError: 401 또는 서명·키 오류 코드.
            BinanceTradeError: 그 외 실패.
        """
        response = await self._send_signed(method, path, params)
        if response.status_code != 200 and '"code":-1021' in response.text[:300]:
            _logger.warning(
                "binance_clock_skew_retry",
                payload={"path": path, "note": "서버시간 오프셋을 다시 재고 한 번 재시도한다"},
            )
            await self._server_offset(refresh=True)
            response = await self._send_signed(method, path, params)
        # 🔴 **얼마나 썼는지 헤더로 안다** (2026-08-29 사고). 한도는 건수가 아니라
        #    가중치라 `klines` 한 번이 `ping` 열 번일 수 있다 — 세지 않으면 한계선까지
        #    얼마나 남았는지 모른 채 달린다. 실패 응답에도 헤더가 실리므로 먼저 읽는다.
        observe("BINANCE", response.headers, limit=BINANCE_MINUTE_WEIGHT, path=path)
        if response.status_code == 200:
            return cast("list[Any] | dict[str, Any]", response.json())
        body = response.text[:300]
        # 🔴 밴이면 **만료 시각을 남긴다** — 밴 중에 계속 부르면 연장된다
        #    (실측 2026-08-29: 2분 → 74분). 재시도하는 쪽이 그때까지 쉴 수 있어야 한다.
        if response.status_code in (418, 429):
            note_ban("BINANCE", body)
        if response.status_code in (401, 403) or '"code":-2015' in body or '"code":-1022' in body:
            raise BinanceAuthError(
                f"바이낸스 인증 실패({method} {path}): {response.status_code} {body}",
                status_code=response.status_code,
            )
        raise BinanceTradeError(
            f"바이낸스 오류 응답({method} {path}): {response.status_code} {body}",
            status_code=response.status_code,
        )

    async def _send_signed(
        self, method: str, path: str, params: dict[str, str] | None
    ) -> httpx.Response:
        """서명해서 한 번 보낸다 — 전송 오류만 예외로 바꾼다."""
        query = dict(params or {})
        query["timestamp"] = str(int(time.time() * 1000) + await self._server_offset())
        query["recvWindow"] = "5000"
        encoded = urlencode(query)
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        url = f"{path}?{encoded}&signature={signature}"
        try:
            return await self._session().request(method, url, headers={"X-MBX-APIKEY": self._key})
        except httpx.HTTPError as exc:
            raise BinanceTradeError(f"바이낸스 전송 실패({method} {path}): {exc}") from exc

    # ── 계정 ─────────────────────────────────────────────────────────

    async def get_account(self) -> dict[str, Any]:
        """계정 요약 — 가용 잔고·포지션 증거금 합 (`/fapi/v2/account`).

        Returns:
            `available`(=availableBalance) · `position_margin`(=totalPositionInitialMargin)
            을 **Gate 호환 키**로 얹은 원문.

        Raises:
            BinanceTradeError: 응답이 객체가 아니다.
        """
        found = await self._request("GET", _ACCOUNT)
        if not isinstance(found, dict):
            raise BinanceTradeError(f"계정 응답이 객체가 아니다: {type(found).__name__}")
        found["available"] = str(found.get("availableBalance", "0"))
        found["position_margin"] = str(found.get("totalPositionInitialMargin", "0"))
        return found

    async def get_positions(self) -> list[dict[str, Any]]:
        """모든 포지션 (`positionRisk`).

        Returns:
            `size`(코인 · 부호)·`margin` 을 Gate 호환 키로 얹은 행들.

        Raises:
            BinanceTradeError: 응답이 배열이 아니다.
        """
        rows = await self._request("GET", _POSITION_RISK)
        if not isinstance(rows, list):
            raise BinanceTradeError(f"포지션 응답이 배열이 아니다: {type(rows).__name__}")
        out: list[dict[str, Any]] = []
        for raw in cast("list[dict[str, Any]]", rows):
            amount = Decimal(str(raw.get("positionAmt", "0") or "0"))
            margin = str(raw.get("isolatedMargin", "") or "")
            if not margin or margin == "0":
                # 교차 마진이면 명목/레버리지로 근사 — 표시·합산용이다 (판정 아님).
                notional = abs(Decimal(str(raw.get("notional", "0") or "0")))
                lever = Decimal(str(raw.get("leverage", "1") or "1"))
                margin = str(notional / lever if lever > 0 else notional)
            out.append({**raw, "size": str(amount), "margin": margin})
        return out

    async def get_position(self, symbol: str) -> dict[str, Any]:
        """한 심볼의 포지션.

        Args:
            symbol: `BTCUSDT`.

        Returns:
            `positionRisk` 행 + `size`(코인 · 부호). 없으면 `size="0"` 행 — 호출자가 None 검사를
            하지 않게 한다.
        """
        rows = await self._request("GET", _POSITION_RISK, params={"symbol": symbol})
        if not isinstance(rows, list) or not rows:
            return {"symbol": symbol, "size": "0"}
        raw = cast("dict[str, Any]", rows[0])
        amount = str(raw.get("positionAmt", "0") or "0")
        return {**raw, "size": amount}

    async def contract(self, symbol: str) -> dict[str, Any]:
        """**주문이 나가는 곳(testnet)** 의 계약 명세 — Gate 호환 키 포함.

        Args:
            symbol: `BTCUSDT`.

        Returns:
            `spec_with_compat` 결과.

        Raises:
            BinanceTradeError: 응답이 객체가 아니거나 심볼이 testnet 명세에 없다.

        Note:
            🔴 명세를 라이브에서 읽고 주문을 testnet 에 내면 호가 단위가 다를 때
            우리 가격이 전부 무효다 (Gate ETH 손절 거절 사고와 같은 원리).

            ⭐ **한 시간 기억한다** (`speccache`). 실패는 기억하지 않으므로 요율 제한
            한 번이 한 시간짜리 공백이 되지는 않는다.
        """

        async def ask() -> dict[str, Any]:
            """거래소에 실제로 묻는다 — 기억통이 비었을 때만 불린다.

            Returns:
                호환 키를 얹은 명세.

            Raises:
                BinanceTradeError: 응답이 객체가 아니거나 심볼이 없다.
            """
            found = await self._request("GET", _EXCHANGE_INFO, params={"symbol": symbol})
            if not isinstance(found, dict):
                raise BinanceTradeError(f"명세 응답이 객체가 아니다({symbol})")
            rows = cast("list[dict[str, Any]]", found.get("symbols") or [])
            row = next((r for r in rows if str(r.get("symbol")) == symbol), None)
            if row is None:
                raise BinanceTradeError(f"{symbol} 이 testnet exchangeInfo 에 없다")
            return spec_with_compat(row)

        # 🔴 **기억통을 거친다** (2026-08-29 사고). 실측 5분에 `exchangeInfo` 130회 —
        #    호가 단위·최소 수량은 상장 규칙이 바뀔 때나 변하는 값인데 걸음마다 물었다.
        #    그 낭비가 요율 한도를 갉아 IP 밴으로 이어졌다.
        return await cached_spec("BINANCE", symbol, ask)

    async def set_leverage(self, symbol: str, leverage: Decimal) -> dict[str, Any]:
        """격리/교차 배율 설정.

        Args:
            symbol: `BTCUSDT`.
            leverage: 배율 (정수로 보낸다).

        Returns:
            거래소 응답 원문.
        """
        found = await self._request(
            "POST", _LEVERAGE, params={"symbol": symbol, "leverage": str(int(leverage))}
        )
        return cast("dict[str, Any]", found)

    async def account_book(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """자금 변동(수입) 이력 — 펀딩·수수료·실현손익 (`/income`).

        Args:
            limit: 최근 몇 건.

        Returns:
            Gate 호환 키로 옮긴 행들. 응답이 배열이 아니면 빈 목록.

        Note:
            Gate 호환 키로 옮긴다: `type`=incomeType · `change`=income ·
            `time`=time(ms) · `contract`=symbol. 해석은 부르는 쪽 몫이다.
        """
        rows = await self._request("GET", _INCOME, params={"limit": str(limit)})
        if not isinstance(rows, list):
            return []
        return [
            {
                "type": str(row.get("incomeType", "")),
                "change": str(row.get("income", "")),
                "time": str(row.get("time", "")),
                "text": str(row.get("info", "")),
                "contract": str(row.get("symbol", "")),
            }
            for row in cast("list[dict[str, Any]]", rows)
        ]

    async def position_closes(self, symbol: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """실현 손익 이력 (`incomeType=REALIZED_PNL`) — 표시용.

        Args:
            symbol: `BTCUSDT`.
            limit: 최근 몇 건.

        Returns:
            Gate 호환 키로 옮긴 행들. 응답이 배열이 아니면 빈 목록.
        """
        rows = await self._request(
            "GET",
            _INCOME,
            params={"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": str(limit)},
        )
        if not isinstance(rows, list):
            return []
        return [
            {
                "time": str(row.get("time", "")),
                "pnl": str(row.get("income", "")),
                "side": "",
                "text": str(row.get("info", "")),
                "max_size": "",
            }
            for row in cast("list[dict[str, Any]]", rows)
        ]

    async def order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        """호가창 — Gate `book_here` 호환 형태 `{bids: [{p, s}], asks: [...]}`.

        Args:
            symbol: `BTCUSDT`.
            limit: 양쪽 각각 몇 단계까지.

        Returns:
            `bids`·`asks` — 각 단계는 `{p: 가격, s: 잔량}`.

        Raises:
            BinanceTradeError: 응답이 객체가 아니다.

        Note:
            ⚠️ 잔량 `s` 가 **코인 수량**이다 (Gate 는 계약 수). 표시용이라 무해하지만
            비교할 때는 단위를 기억한다.
        """
        found = await self._request("GET", _DEPTH, params={"symbol": symbol, "limit": str(limit)})
        if not isinstance(found, dict):
            raise BinanceTradeError(f"호가 응답이 객체가 아니다({symbol})")
        bids = cast("list[list[str]]", found.get("bids") or [])
        asks = cast("list[list[str]]", found.get("asks") or [])
        return {
            "bids": [{"p": str(b[0]), "s": str(b[1])} for b in bids],
            "asks": [{"p": str(a[0]), "s": str(a[1])} for a in asks],
        }

    # ── 주문 ─────────────────────────────────────────────────────────

    def _remember(self, payload: dict[str, Any], symbol: str) -> None:
        for key in ("orderId", "clientOrderId"):
            value = str(payload.get(key, "") or "")
            if value:
                self._symbol_of[value] = symbol

    async def place_order(
        self,
        symbol: str,
        quantity: Decimal,
        *,
        idempotency_key: str,
        price: Decimal | None = None,
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> dict[str, Any]:
        """주문을 낸다.

        Args:
            symbol: `BTCUSDT`.
            quantity: **부호 있는 코인 수량.** 양수 매수, 음수 매도. 0 은 거부.
            idempotency_key: 멱등키 → `newClientOrderId` (36자 초과는 잘리며 로그).
            price: 지정가. None 이면 시장가.
            reduce_only: 포지션 감소 전용 — 익절·손절·청산은 참이어야 한다.
            post_only: 지정가를 GTX 로 — 크로스면 **EXPIRED 로 거부**된다 (메이커 보장).

        Returns:
            거래소 주문 응답 원문 (`orderId`·`status`·`executedQty`·`avgPrice` …).

        Raises:
            ValueError: 수량 0 또는 빈 멱등키.
            BinanceAuthError / BinanceTradeError: 호출 실패.

        Note:
            🔴 **재시도하지 않는다** — 실패하면 던진다. 재시도 전 체결 확인은 부르는 쪽
            몫이다 (절대 규칙 #6).
        """
        if quantity == 0:
            raise ValueError("수량이 0 이다 — 방향이 없는 주문은 뜻이 없다")
        if not idempotency_key:
            raise ValueError("멱등키가 비었다 (절대 규칙 #6)")
        params: dict[str, str] = {
            "symbol": symbol,
            "side": "BUY" if quantity > 0 else "SELL",
            "quantity": price_text(abs(quantity)),
            "newClientOrderId": clip_client_id(idempotency_key),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if price is None:
            params["type"] = "MARKET"
        else:
            params["type"] = "LIMIT"
            params["price"] = price_text(price)
            params["timeInForce"] = "GTX" if post_only else "GTC"
        _logger.info(
            "binance_order_submit",
            payload={
                "symbol": symbol,
                "quantity": str(quantity),
                "market": price is None,
                "reduce_only": reduce_only,
                "post_only": post_only,
                "testnet": self.is_testnet,
            },
        )
        found = await self._request("POST", _ORDER, params=params)
        payload = cast("dict[str, Any]", found)
        self._remember(payload, symbol)
        return payload

    async def place_stop(self, symbol: str, trigger: Decimal, *, long: bool) -> str:
        """브로커측 손절 — **Algo Order API** (`STOP_MARKET` + `closePosition`).

        Args:
            symbol: `BTCUSDT`.
            trigger: 발동가.
            long: 롱 포지션이면 SELL 스탑, 숏이면 BUY 스탑.

        Returns:
            조건부(algo) 주문 id.

        Note:
            🔴 `/fapi/v1/order` 에 STOP_MARKET 을 보내면 **-4120** 으로 거부된다
            (2026-08-25 testnet 실측) — 바이낸스가 조건부를 Algo API 로 옮겼다.
            실측 규격: `algotype=CONDITIONAL` + `type=STOP_MARKET` + `triggerPrice`.
            ⭐ `GTE_GTC` 라 **24시간 만료가 없다** (Gate 조건부의 재장착 문제 없음).
            "걸었다"는 기억 대신 거래소 목록으로 확인하는 원칙은 같다.
        """
        params = {
            "symbol": symbol,
            "side": "SELL" if long else "BUY",
            "algotype": "CONDITIONAL",
            "type": "STOP_MARKET",
            "triggerPrice": price_text(trigger),
            "closePosition": "true",
            "workingType": "CONTRACT_PRICE",
        }
        found = await self._request("POST", _ALGO_ORDER, params=params)
        payload = cast("dict[str, Any]", found)
        algo_id = str(payload.get("algoId", ""))
        if algo_id:
            self._symbol_of[algo_id] = symbol
        return algo_id

    async def close_position(self, symbol: str, *, idempotency_key: str) -> dict[str, Any]:
        """포지션을 시장가로 전량 닫는다.

        Args:
            symbol: `BTCUSDT`.
            idempotency_key: 멱등키.

        Returns:
            청산 주문 응답 원문.

        Raises:
            BinanceTradeError: 닫을 포지션이 없다 — 0 수량 주문을 내지 않는다.

        Note:
            🔴 수량은 **거래소가 말한 보유량**이다 — 우리가 세면 부분체결·펀딩 만큼
            어긋나 반대 포지션이 열린다 (Gate 와 같은 원칙 · 여기는 size=0 API 가 없어
            positionAmt 를 읽어 reduceOnly 반대 주문으로 보낸다).
        """
        held = await self.get_position(symbol)
        amount = Decimal(str(held.get("size", "0") or "0"))
        if amount == 0:
            raise BinanceTradeError(f"{symbol} 에 닫을 포지션이 없다")
        return await self.place_order(
            symbol, -amount, idempotency_key=idempotency_key, reduce_only=True
        )

    async def list_orders(self, symbol: str, status: str = "open") -> list[dict[str, Any]]:
        """주문 목록 — `open`(미결 지정가) 또는 `finished`(끝난 것).

        Args:
            symbol: `BTCUSDT`.
            status: `open` 또는 `finished`.

        Returns:
            Gate 호환 키를 얹은 행들. 응답이 배열이 아니면 빈 목록.

        Note:
            Gate 호환 키를 얹는다: `id`·`size`(계약 아님 — **코인 수량** · 부호) ·
            `left`·`price`·`fill_price`·`text`(=clientOrderId · **판 표식이 여기 산다**) ·
            `status`·`is_reduce_only`·`create_time`. 조건부 유형은 `open` 에서 뺀다
            (`list_stops` 가 맡는다).
        """
        if status == "open":
            rows = await self._request("GET", _OPEN_ORDERS, params={"symbol": symbol})
        else:
            rows = await self._request("GET", _ALL_ORDERS, params={"symbol": symbol, "limit": "50"})
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for raw in cast("list[dict[str, Any]]", rows):
            kind = str(raw.get("type", ""))
            state = str(raw.get("status", ""))
            if status == "open" and kind in _STOP_TYPES:
                continue
            if status == "finished" and state in {"NEW", "PARTIALLY_FILLED"}:
                continue
            side = str(raw.get("side", ""))
            qty = Decimal(str(raw.get("origQty", "0") or "0"))
            filled = Decimal(str(raw.get("executedQty", "0") or "0"))
            signed = qty if side == "BUY" else -qty
            self._remember(raw, symbol)
            out.append(
                {
                    **raw,
                    "id": str(raw.get("orderId", "")),
                    "size": str(signed),
                    "left": str(qty - filled),
                    "price": str(raw.get("price", "")),
                    "fill_price": str(raw.get("avgPrice", "")),
                    "text": str(raw.get("clientOrderId", "")),
                    "is_reduce_only": str(raw.get("reduceOnly", "")),
                    "create_time": str(raw.get("time", "")),
                    "finish_time": str(raw.get("updateTime", "")),
                    "finish_as": state.lower(),
                }
            )
        if status == "finished":
            out.reverse()  # 최근 것이 앞
        return out

    async def list_stops(self, symbol: str) -> list[dict[str, Any]]:
        """조건부(algo) 주문들 — Gate 호환 형태 (`trigger.price` 중첩 포함).

        Args:
            symbol: `BTCUSDT`.

        Returns:
            열린 조건부 주문 행들.

        Raises:
            ValueError: 바이낸스 표기가 아니다 — 무효 심볼이면 거래소가 **전 계정** 조건부를
                돌려주므로 호출부 실수 단계에서 끊는다.

        Note:
            Algo API 실측 형태: `algoId`·`triggerPrice`·`orderType`·`quantity`(closePosition
            는 0)·`clientAlgoId`(자동 생성 — 우리 표식이 아니다 · run_of_text 가 걸러낸다).
        """
        if "_" in symbol:
            # 바이낸스는 무효 심볼이면 조용히 전 계정 조건부를 돌려준다 (2026-08-25 실측:
            # XRP_USDT 로 물으니 XRP+DOGE 전부). 남의 스탑을 제 것으로 세는 사고를
            # 호출부 실수 단계에서 끊는다.
            raise ValueError(f"바이낸스 심볼이 아니다: {symbol!r}")
        rows = await self._request("GET", _OPEN_ALGO, params={"symbol": symbol})
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for raw in cast("list[dict[str, Any]]", rows):
            algo_id = str(raw.get("algoId", ""))
            if algo_id:
                self._symbol_of[algo_id] = symbol
            side = str(raw.get("side", ""))
            qty = Decimal(str(raw.get("quantity", "0") or "0"))
            signed = qty if side == "BUY" else -qty
            out.append(
                {
                    **raw,
                    "id": algo_id,
                    "trigger": {"price": str(raw.get("triggerPrice", "")), "expiration": ""},
                    "initial": {
                        "size": str(signed),
                        "reduce_only": "true",  # closePosition 조건부 — 전량 감소 전용
                        "text": str(raw.get("clientAlgoId", "")),
                    },
                    "text": str(raw.get("clientAlgoId", "")),
                    "create_time": str(raw.get("bookTime", "") or raw.get("createTime", "")),
                }
            )
        return out

    async def find_order(self, order_id: str, *, symbol: str = "") -> dict[str, Any] | None:
        """주문 하나 — id 또는 멱등키로 (재시도 판단용 · 절대 규칙 #6).

        Args:
            order_id: 거래소 id(숫자) 또는 멱등키.
            symbol: `BTCUSDT`. 이 클라이언트가 낸 주문이면 기억한 매핑으로 생략 가능.

        Returns:
            주문 원문. 거래소가 모르면 None.

        Raises:
            BinanceTradeError: 심볼을 모른다 — "없다" 와 "못 찾는다" 는 다르다.

        Note:
            🔴 바이낸스는 조회에 **symbol 이 필수**다. 이 클라이언트가 기억한 매핑에
            없고 `symbol` 도 안 주면 — 모른다고 말한다 (None 이 아니라 예외:
            "없다"와 "못 찾는다"는 다르다).
        """
        found_symbol = symbol or self._symbol_of.get(order_id, "")
        if not found_symbol:
            raise BinanceTradeError(
                f"주문 {order_id} 의 심볼을 모른다 — 재시작 후라면 find_order(key, symbol=...) 로 "
                "심볼을 함께 넘긴다"
            )
        key = "orderId" if order_id.isdigit() else "origClientOrderId"
        try:
            found = await self._request(
                "GET", _ORDER, params={"symbol": found_symbol, key: order_id}
            )
        except BinanceTradeError as exc:
            if exc.status_code == 400 and "-2013" in str(exc):  # Order does not exist
                return None
            raise
        payload = cast("dict[str, Any]", found)
        self._remember(payload, found_symbol)
        return payload

    async def cancel_order(self, order_id: str, *, symbol: str = "") -> dict[str, Any]:
        """주문 취소 — 이미 체결됐으면 거래소가 거부하고 그 예외를 그대로 올린다.

        Args:
            order_id: 거래소 id(숫자) 또는 멱등키.
            symbol: `BTCUSDT`. 이 클라이언트가 낸 주문이면 기억한 매핑으로 생략 가능.

        Returns:
            취소 응답 원문.

        Raises:
            BinanceTradeError: 심볼을 모른다 — 바이낸스는 취소에 심볼이 필수다.
        """
        found_symbol = symbol or self._symbol_of.get(order_id, "")
        if not found_symbol:
            raise BinanceTradeError(
                f"주문 {order_id} 의 심볼을 모른다 — 취소하려면 심볼이 필요하다"
            )
        key = "orderId" if order_id.isdigit() else "origClientOrderId"
        found = await self._request(
            "DELETE", _ORDER, params={"symbol": found_symbol, key: order_id}
        )
        return cast("dict[str, Any]", found)

    async def cancel_stop(self, order_id: str, *, symbol: str = "") -> dict[str, Any]:
        """조건부(algo) 취소 — `DELETE /fapi/v1/algoOrder` (실측 2026-08-25).

        Args:
            order_id: 조건부(algo) 주문 id.
            symbol: `BTCUSDT`. 기억한 매핑에 있으면 생략 가능.

        Returns:
            취소 응답 원문.

        Raises:
            BinanceTradeError: 심볼을 모른다.
        """
        found_symbol = symbol or self._symbol_of.get(order_id, "")
        if not found_symbol:
            raise BinanceTradeError(
                f"조건부 {order_id} 의 심볼을 모른다 — 취소하려면 심볼이 필요하다"
            )
        found = await self._request(
            "DELETE", _ALGO_ORDER, params={"symbol": found_symbol, "algoId": order_id}
        )
        return cast("dict[str, Any]", found)
