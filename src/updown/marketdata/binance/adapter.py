"""바이낸스 USDT-M — **조회 전용** 어댑터 (T62 P1).

Gate 어댑터의 미러다: 캔들·시세·호가·펀딩·계약 명세를 조회하고, 주문 메서드는
전부 예외를 던진다 (절대 규칙 #0 의 2차 방어선 — 주문 경로는 OrderGateway 독점).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.market import (
    Balance,
    MarketSession,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    Quote,
)
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.marketdata.adapter import Capability
from updown.marketdata.binance.client import BinanceClient
from updown.marketdata.binance.mapping import (
    BinanceMappingError,
    interval_of,
    interval_seconds,
    spec_with_compat,
    to_candle,
    to_symbol,
)
from updown.marketdata.binance.ws import BinanceCandleStream

_KLINES = "/fapi/v1/klines"
_BOOK_TICKER = "/fapi/v1/ticker/bookTicker"
_PRICE = "/fapi/v1/ticker/price"
_DEPTH = "/fapi/v1/depth"
_PREMIUM = "/fapi/v1/premiumIndex"
_EXCHANGE_INFO = "/fapi/v1/exchangeInfo"

#: 한 페이지 캔들 상한 (바이낸스 명세 1500).
CANDLE_LIMIT = 1500


def klines_limit_for(cursor_ms: int, end_ms: int, step_ms: int) -> int:
    """이번 요청에 **실제로 필요한** 봉 수 — `limit` 이 weight 를 정한다 (T217 · 2026-09-04).

    바이낸스 klines weight: limit <100 → 1 · 100~499 → 2 · 500~1000 → 5 · >1000 → 10.
    지금까지 항상 1500 을 보내 **5봉이 필요해도 10 을 냈다** — 러너의 30초 refresh, 1초 forming,
    콘솔 차트가 전부 그랬고, 그것이 분당 한도 184% 초과의 절반이었다 (실측 · T217 인벤토리).

    Args:
        cursor_ms: 이번 페이지 시작 (ms).
        end_ms: 구간 끝 (ms · 포함).
        step_ms: 봉 간격 (ms).

    Returns:
        1 이상 `CANDLE_LIMIT` 이하. 구간 끝 봉 하나를 더 담아 경계 봉을 놓치지 않는다.
    """
    if step_ms <= 0 or end_ms < cursor_ms:
        return 1
    need = (end_ms - cursor_ms) // step_ms + 2
    return max(1, min(CANDLE_LIMIT, int(need)))


class OrderPathNotAvailableError(NotImplementedError):
    """조회 전용 어댑터에 주문을 시도했다 — 게이트 우회의 두 번째 방어선."""


class BinanceAdapter:
    """바이낸스 무기한 — 조회 전용.

    Note:
        ⛔ `WS`·`CONDITIONAL_ORDERS` 는 능력표에 없다 — 스트림·조건부는 P2 에서
        검증한 뒤에 알린다. 능력표는 곧 계약이라, 적으면 상위가 그 경로를 믿는다.
    """

    def __init__(self, client: BinanceClient, *, ws_url: str | None = None) -> None:
        """어댑터를 만든다.

        Args:
            client: 공개 조회 클라이언트 (자격증명 없음).
            ws_url: 캔들 스트림 소켓 URL. 🔴 **REST 베이스와 같은 곳**이어야 한다 —
                다르면 과거 봉과 진행 중 봉이 다른 시장에서 와 오른쪽 끝에
                이음매가 생긴다 (`binance/venue.py` 참고). 비우면 스트림 기본값.

        Note:
            ⛔ 클라이언트를 여기서 만들지 않는다 — 획득은 `marketdata/provider.py` 독점.
        """
        self._client = client
        # 🔴 **스트림도 같은 곳을 본다.** 조회 베이스만 바꾸고 소켓을 라이브로
        #    두면 과거 봉과 진행 중 봉이 **다른 시장**에서 오고, 오른쪽 끝에서
        #    0.05% 짜리 이음매가 생긴다 (실측 괴리).
        self._ws_url = ws_url
        self._specs: dict[str, dict[str, Any]] = {}

    @property
    def capabilities(self) -> frozenset[Capability]:
        """이 어댑터가 할 수 있는 것 — 선물이라 숏·레버리지·펀딩이 열린다."""
        return frozenset(
            {
                Capability.DERIVATIVES,
                # 캔들 웹소켓 — 빠지면 봉 캐시가 코인 판을 감싼다 (T240·T251).
                Capability.WS,
                Capability.LEVERAGE,
                Capability.SHORT,
                Capability.FUNDING,
                Capability.ORDERBOOK,
            }
        )

    async def funding_rate(self, instrument: Instrument) -> Decimal | None:
        """지금 8h 펀딩 요율 (`premiumIndex.lastFundingRate` · 0.8.1 펀딩캡 입력).

        Args:
            instrument: 종목.

        Returns:
            요율. 못 읽으면 None — 게이트가 잠잔다 (보수 방향).
        """
        payload = await self._client.get_json(
            "/fapi/v1/premiumIndex", params={"symbol": to_symbol(instrument)}
        )
        if not isinstance(payload, dict):
            return None
        raw = cast("dict[str, object]", payload).get("lastFundingRate")
        try:
            return Decimal(str(raw))
        except (ArithmeticError, ValueError, TypeError):
            return None

    async def get_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """기간 내 캔들 — `ts` 오름차순 · UTC aware.

        Args:
            instrument: 대상 종목.
            timeframe: 시간축.
            start: 시작 (포함).
            end: 끝 (포함).

        Returns:
            캔들 목록. 구간에 봉이 없으면 빈 리스트.

        Raises:
            BinanceMappingError: naive 시각·역전 구간·응답 형식 오류.
            BinanceApiError: 호출 실패.

        Note:
            페이징(1500봉/호출)은 여기서 숨긴다. 커서가 전진하지 않으면 멈춘다 —
            조용한 무한 루프 방지 (Gate 어댑터와 같은 방어). 거래소가 오름차순으로
            주지만 믿지 않고 정렬한다.
        """
        for name, moment in (("start", start), ("end", end)):
            if moment.tzinfo is None:
                raise BinanceMappingError(f"{name} 가 naive 다 (spec §12.3): {moment!r}")
        if start > end:
            raise BinanceMappingError(
                f"구간이 역전됐다: start={start.isoformat()} > end={end.isoformat()}"
            )
        symbol = to_symbol(instrument)
        interval = interval_of(timeframe)
        step_ms = interval_seconds(timeframe) * 1000
        end_ms = int(end.timestamp() * 1000)

        seen: dict[datetime, Candle] = {}
        cursor = int(start.timestamp() * 1000)
        while cursor <= end_ms:
            # ⭐ 필요한 만큼만 — `limit` 이 곧 weight 다 (`klines_limit_for` 주석).
            limit = klines_limit_for(cursor, end_ms, step_ms)
            payload = await self._client.get_json(
                _KLINES,
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": str(cursor),
                    "endTime": str(end_ms),
                    "limit": str(limit),
                },
            )
            if not isinstance(payload, list):
                raise BinanceMappingError(f"캔들 응답이 배열이 아니다: {type(payload).__name__}")
            if not payload:
                break
            newest = 0
            for row in payload:
                candle = to_candle(row, instrument, timeframe)
                newest = max(newest, int(candle.ts.timestamp() * 1000))
                if start <= candle.ts <= end:
                    seen[candle.ts] = candle
            advanced = newest + step_ms
            if advanced <= cursor:
                break  # 커서가 안 움직였다 — 같은 페이지 반복 방지
            cursor = advanced
            if len(payload) < limit:
                break
        return [seen[key] for key in sorted(seen)]

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재 시세 — 최종가 + 최우선 호가.

        Args:
            instrument: 종목.

        Returns:
            시세 스냅샷. `as_of` 는 우리가 잰 시각이다.

        Raises:
            BinanceMappingError: 응답이 다른 심볼이거나 형식이 다른 경우.
            BinanceApiError: 호출 실패.
        """
        symbol = to_symbol(instrument)
        book = await self._client.get_json(_BOOK_TICKER, params={"symbol": symbol})
        last = await self._client.get_json(_PRICE, params={"symbol": symbol})
        if not isinstance(book, dict) or not isinstance(last, dict):
            raise BinanceMappingError(f"시세 응답 형식이 다르다({symbol})")
        for row in (book, last):
            if str(row.get("symbol", "")) != symbol:
                raise BinanceMappingError(
                    f"시세가 다른 심볼을 돌려줬다: 요청 {symbol} · 응답 {row.get('symbol')!r}"
                )
        return Quote(
            instrument=instrument,
            last_price=Decimal(str(last["price"])),
            bid=Decimal(str(book["bidPrice"])),
            ask=Decimal(str(book["askPrice"])),
            as_of=datetime.now(UTC),
        )

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """호가창 (20단계).

        Args:
            instrument: 종목.

        Returns:
            호가창. 수량은 이미 기초자산 단위다.

        Raises:
            BinanceMappingError: 응답 형식 오류.
            BinanceApiError: 호출 실패.

        Note:
            수량은 이미 기초자산 단위다 — Gate 의 계약수x승수 변환이 없다.
        """
        symbol = to_symbol(instrument)
        payload = await self._client.get_json(_DEPTH, params={"symbol": symbol, "limit": "20"})
        if not isinstance(payload, dict):
            raise BinanceMappingError(f"호가 응답이 객체가 아니다: {type(payload).__name__}")
        bids = cast("list[list[str]]", payload.get("bids") or [])
        asks = cast("list[list[str]]", payload.get("asks") or [])
        if not bids or not asks:
            raise BinanceMappingError(f"호가가 비었다({symbol}): bids={len(bids)} asks={len(asks)}")
        levels = tuple(
            OrderBookLevel(
                bid_price=Decimal(str(bid[0])),
                bid_size=Decimal(str(bid[1])),
                ask_price=Decimal(str(ask[0])),
                ask_size=Decimal(str(ask[1])),
            )
            for bid, ask in zip(bids, asks, strict=False)
        )
        return OrderBook(instrument=instrument, levels=levels, as_of=datetime.now(UTC))

    async def get_funding_rate(self, instrument: Instrument) -> Decimal:
        """다음 펀딩 비율 (`premiumIndex.lastFundingRate`).

        Args:
            instrument: 종목.

        Returns:
            요율 (예: 0.0001 = 0.01%/8h).

        Raises:
            BinanceMappingError: 응답 형식 오류.
            BinanceApiError: 호출 실패.
        """
        symbol = to_symbol(instrument)
        payload = await self._client.get_json(_PREMIUM, params={"symbol": symbol})
        if not isinstance(payload, dict) or "lastFundingRate" not in payload:
            raise BinanceMappingError(f"펀딩 응답 형식이 다르다({symbol}): {payload!r}")
        return Decimal(str(payload["lastFundingRate"]))

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 — 라운딩·수량 한계의 단일 출처.

        Args:
            instrument: 종목.

        Returns:
            바이낸스 필터 원문 + **Gate 호환 키**:
            `quanto_multiplier`(=stepSize) · `order_size_min`(=minQty/stepSize · 정수) ·
            `order_price_round`(=tickSize). 러너의 정수 계약 사이징이 그대로 돈다 —
            "계약 1개 = stepSize 코인" 으로 읽으면 두 거래소가 같은 산수가 된다.

        Raises:
            BinanceMappingError: 심볼이 명세에 없거나 필터가 빠진 경우.
            BinanceApiError: 호출 실패.
        """
        symbol = to_symbol(instrument)
        if symbol in self._specs:
            return self._specs[symbol]
        payload = await self._client.get_json(_EXCHANGE_INFO, params={"symbol": symbol})
        if not isinstance(payload, dict):
            raise BinanceMappingError(f"명세 응답이 객체가 아니다({symbol})")
        rows = cast("list[dict[str, Any]]", payload.get("symbols") or [])
        found = next((row for row in rows if str(row.get("symbol")) == symbol), None)
        if found is None:
            raise BinanceMappingError(f"{symbol} 이 exchangeInfo 에 없다")
        spec = spec_with_compat(found)
        self._specs[symbol] = spec
        return spec

    _NO_FRAMES: ClassVar[frozenset[Timeframe]] = frozenset({Timeframe.S10, Timeframe.S30})
    """바이낸스 USDT-M 에 없는 kline 축 — 최소 간격이 1m 다 (T62 P2b 실측)."""

    def supported_frames(self, frames: Sequence[Timeframe]) -> tuple[Timeframe, ...]:
        """요청한 시간축 중 바이낸스가 캔들을 주는 것만 (순서 유지).

        Args:
            frames: 원하는 시간축들.

        Returns:
            10s·30s 를 뺀 나머지.

        Note:
            10s·30s 가 빠진다. 화면 최하위 축이 1m 로 시작할 뿐, 판정 축(플레이북
            timeframe)은 영향이 없다 (T62 P2b).
        """
        return tuple(frame for frame in frames if frame not in self._NO_FRAMES)

    def candle_stream(
        self, instruments: Sequence[Instrument], timeframe: Timeframe, multiplier: Decimal
    ) -> BinanceCandleStream:
        """바이낸스 라이브 캔들 스트림 (T63 §2b) — WS 침묵 시 REST 폴링 폴백 포함.

        Args:
            instruments: 구독할 종목들.
            timeframe: 봉 간격.
            multiplier: 계약 승수.

        Returns:
            아직 열지 않은 스트림. 주문 환경(테스트넷/라이브)의 WS URL 로 붙는다.
        """
        if self._ws_url is None:
            return BinanceCandleStream(instruments, timeframe, multiplier)
        return BinanceCandleStream(instruments, timeframe, multiplier, url=self._ws_url)

    def interval_seconds(self, timeframe: Timeframe) -> int:
        """시간축의 초 길이 (피드 빌더가 쓴다).

        Args:
            timeframe: 시간축.

        Returns:
            초. 피드 빌더가 다음 봉 마감 시각을 계산한다.
        """
        return interval_seconds(timeframe)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — 무기한은 24시간 장이다 (펀딩은 비용 층의 일이다).

        Args:
            instrument: 종목. 표기 검증만 한다.

        Returns:
            `ALWAYS_OPEN` · 주문 허용. 인터페이스를 맞추기 위해 있다.
        """
        to_symbol(instrument)
        return MarketStatus(
            instrument=instrument,
            session=MarketSession.ALWAYS_OPEN,
            is_order_allowed=True,
            as_of=datetime.now(UTC),
            next_open=None,
            next_close=None,
        )

    async def get_balance(self) -> Balance:
        """미지원 — 잔고는 서명이 필요하고, 이 어댑터는 키가 없다.

        Raises:
            OrderPathNotAvailableError: 항상. 페이크머니 잔고는 테스트넷 키를 든 클라이언트가
                읽는다.
        """
        raise OrderPathNotAvailableError(
            "BinanceAdapter 는 잔고를 조회할 수 없다 (조회 전용 · 키 없음)"
        )

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """미지원 — 주문 경로는 `OrderGateway` 가 독점한다 (절대 규칙 #0).

        Args:
            order: 주문 요청 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상 — 게이트를 우회한 코드가 조용히 주문하지 못하게.
        """
        raise OrderPathNotAvailableError(
            f"BinanceAdapter 는 주문을 낼 수 없다 (조회 전용). "
            f"요청={order.instrument.symbol} {order.side.value} — "
            "주문 경로는 OrderGateway 가 독점한다"
        )

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """미지원 — 취소도 주문 경로다.

        Args:
            broker_order_id: 브로커 주문 id (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.
        """
        raise OrderPathNotAvailableError(
            f"BinanceAdapter 는 주문을 취소할 수 없다 (조회 전용). id={broker_order_id}"
        )

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """미지원 — 주문 조회도 서명이 필요하다.

        Args:
            broker_order_id: 브로커 주문 id (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상 — 그럴듯한 상태를 지어내면 재시도 전 체결 확인
                (규칙 #6)이 거짓 답을 받는다.
        """
        raise OrderPathNotAvailableError(
            f"BinanceAdapter 는 주문 상태를 조회할 수 없다 (조회 전용). id={broker_order_id}"
        )
