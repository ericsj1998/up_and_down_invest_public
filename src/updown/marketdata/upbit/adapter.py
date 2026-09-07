"""`UpbitAdapter` — 코인 현물 **조회 전용** (spec §4.2, P0-7).

`capabilities = {SPOT, WS}` 다. 국내 규제상 원화 거래소는 마진·레버리지·선물을 제공하지
않는다 (spec §4.2, §12.8).

> ⚠️ **주문 경로가 없다.** `submit_order`/`cancel_order`/`get_order_status`/`get_balance` 는
> 명시적 예외를 던진다. Phase 0~1 에는 실주문이 존재하지 않으며(plan D-12), **조용히 성공한
> 척하는 경로를 만들지 않는다** (spec §7).
>
> 절대 규칙 #0 — 이 클래스를 주문 경로에서 직접 생성·import 하지 않는다.
> 반드시 `execution.gateway.OrderGateway` 를 통한다 (spec §12.4).
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, MarketListing, Timeframe
from updown.common.domain.market import Balance, MarketStatus, OrderBook, Quote
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import Capability
from updown.marketdata.upbit.client import UpbitApiError, UpbitClient
from updown.marketdata.upbit.mapping import (
    MAX_CANDLE_COUNT,
    candle_path,
    interval_seconds,
    to_candle,
    to_iso_z,
    to_market_code,
    to_market_status,
    to_orderbook,
    to_quote,
)

_logger = get_logger("marketdata.upbit.adapter")

#: 커서 반복 안전 상한. 이 횟수를 넘으면 예외를 던진다.
#:
#: 무한 루프 방지가 목적이다. `to` 가 exclusive 라 정상 동작에서는 매 페이지가 반드시
#: 과거로 전진하지만, 업비트가 `to` 를 inclusive 로 바꾸면 같은 페이지를 영원히 받는다.
#: 그때 조용히 도는 대신 **시끄럽게 죽는 편**이 낫다 (spec §7).
MAX_CURSOR_PAGES = 200

#: `/ticker` 한 번에 묶어 물을 종목 수.
#:
#: 종목마다 부르면 281회다. 업비트는 `markets=` 에 쉼표로 여러 개를 받으므로 묶는다.
#: URL 길이를 생각해 100개로 자른다 (코드 하나가 10자 남짓이라 1KB 정도다).
TICKER_BATCH = 100


def _optional_decimal(payload: dict[str, Any] | None, key: str) -> Decimal | None:
    """있으면 Decimal, 없거나 못 읽으면 None.

    Args:
        payload: 응답 행. None 이면 그 종목의 시세를 못 받은 것이다.
        key: 칸 이름.

    Returns:
        값 또는 None.

    Note:
        **0 으로 때우지 않는다.** 거래대금 0 과 "거래대금을 모른다"는 다른 사실이고,
        0 으로 채우면 정렬에서 거래가 없는 종목처럼 맨 아래로 밀린다 (절대 규칙 #8).
    """
    if payload is None or key not in payload:
        return None
    try:
        return Decimal(str(payload[key]))
    except (InvalidOperation, TypeError, ValueError):
        return None


class OrderPathNotAvailableError(NotImplementedError):
    """업비트 어댑터에는 주문 경로가 없다 (P0-7 조회 전용).

    Note:
        `NotImplementedError` 를 상속하는 이유: "아직 구현 안 됨"이 정확한 의미이고,
        호출부가 이것을 성공으로 오해할 여지가 없다. **조용히 None 을 반환하거나 빈
        결과를 주지 않는다** (spec §7 조용한 실패 금지).

        Phase 2 에서 주문을 열 때도 이 클래스에 직접 붙이지 않는다 — `OrderGateway` 가
        어댑터 획득을 독점하며(절대 규칙 #0), 페이퍼 경로부터 연다 (P2-1).
    """


class UpbitAdapter:
    """업비트 시세 조회 어댑터 (`BrokerAdapter` 구현 중 조회 부분).

    Note:
        `BrokerAdapter` 프로토콜을 **구조적으로** 만족한다 (Protocol 이므로 상속하지
        않는다). 주문 메서드는 시그니처를 지키되 예외를 던진다 — 프로토콜을 어기면
        상위 계층이 어댑터를 교체할 수 없어 추상화가 무의미해진다.
    """

    def __init__(self, client: UpbitClient) -> None:
        """어댑터를 만든다.

        Args:
            client: HTTP 클라이언트. 스로틀·재시도는 클라이언트의 책임이다.
        """
        self._client = client

    @property
    def capabilities(self) -> frozenset[Capability]:
        """지원 기능 (spec §4.2).

        Returns:
            `{SPOT, WS, ORDERBOOK}`.

        Note:
            `CONDITIONAL_ORDERS` 가 없다 — 업비트는 브로커측 스탑 주문을 제공하지 않으므로
            "서버 다운 중 손절가 도달"(spec §7)의 이중화 수단이 코인에는 **없다**. 이는
            어댑터의 한계가 아니라 거래소의 사실이며, 그래서 워치독·재기동 복구 루틴이
            코인에서는 유일한 방어선이다 (spec §12.6).

            `ORDERBOOK` 은 **키 없이** 되는 공개 엔드포인트라 P0-7 의 안전 속성(자격증명
            미사용)을 깨지 않는다. 이것이 있어서 슬리피지를 가정 대신 측정할 수 있다
            (spec §12.7).
        """
        return frozenset({Capability.SPOT, Capability.WS, Capability.ORDERBOOK})

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    async def get_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """기간 내 캔들을 **오름차순**으로 반환한다.

        Args:
            instrument: 대상 종목.
            timeframe: 시간축.
            start: 조회 시작 (UTC, 포함).
            end: 조회 끝 (UTC, 포함).

        Returns:
            `ts` 오름차순, 전부 UTC aware. `start`~`end` 밖의 봉은 잘라낸다.

        Raises:
            ValueError: `start`/`end` 가 naive 이거나 `start > end`.
            UpbitApiError: API 실패.
            UnknownMarketError: 없는 마켓.

        Note:
            업비트는 **내림차순**으로 주고 1회 최대 **200개**이며, 초과 요청은 에러 없이
            200개로 잘린다 (실측). 그래서 커서를 과거로 돌려 채운 뒤 마지막에 뒤집는다.

            커서는 `to` 파라미터이고 **exclusive** 다 — 받은 페이지의 가장 오래된 봉의
            `ts` 를 다음 `to` 로 넘기면 그 봉이 다시 오지 않으므로 **중복 제거가 필요 없다**.
        """
        self._require_utc(start, "start")
        self._require_utc(end, "end")
        if start > end:
            raise ValueError(f"start 가 end 보다 늦다: {start.isoformat()} > {end.isoformat()}")

        market = to_market_code(instrument)
        path = candle_path(timeframe)
        step = interval_seconds(timeframe)

        collected: dict[datetime, Candle] = {}
        # `to` 는 exclusive 이므로 end 봉 자체를 받으려면 한 칸 뒤를 가리켜야 한다.
        cursor = end.astimezone(UTC).timestamp() + step

        pages = 0
        for _ in range(MAX_CURSOR_PAGES):
            pages += 1
            params = {
                "market": market,
                "count": str(MAX_CANDLE_COUNT),
                "to": to_iso_z(datetime.fromtimestamp(cursor, tz=UTC)),
            }
            rows = await self._get_list(path, group="candles", params=params)
            if not rows:
                break

            candles = [to_candle(row, instrument, timeframe) for row in rows]
            oldest = min(candle.ts for candle in candles)
            for candle in candles:
                collected[candle.ts] = candle

            if oldest <= start:
                break

            next_cursor = oldest.timestamp()
            if next_cursor >= cursor:
                # `to` 가 exclusive 라는 전제가 깨졌다. 조용히 돌면 무한 루프다.
                raise UpbitApiError(
                    f"커서가 전진하지 않는다 — `to` 가 inclusive 로 바뀐 것일 수 있다 "
                    f"(path={path}, cursor={cursor}, oldest={oldest.isoformat()}). "
                    "docs/platform/upbit_api_notes.md §4 를 재확인하라"
                )
            cursor = next_cursor
        else:
            raise UpbitApiError(
                f"커서 반복이 상한({MAX_CURSOR_PAGES}페이지)을 넘었다 — "
                f"구간이 너무 넓거나 규격이 바뀌었다 (path={path}, "
                f"start={start.isoformat()}, end={end.isoformat()})"
            )

        result = sorted(
            (candle for ts, candle in collected.items() if start <= ts <= end),
            key=lambda candle: candle.ts,
        )
        _logger.info(
            "upbit_candles_fetched",
            payload={
                "market": market,
                "timeframe": timeframe.value,
                "pages": pages,
                "received": len(collected),
                "in_range": len(result),
            },
        )
        return result

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재 시세 스냅샷.

        Args:
            instrument: 대상 종목.

        Returns:
            `bid`/`ask` 는 None 이다 — 업비트 `/ticker` 에 호가가 없다 (mapping 참조).

        Raises:
            UpbitApiError: API 실패 또는 빈 응답.
            UnknownMarketError: 없는 마켓.

        Note:
            `as_of` 는 **요청 직전에 우리가 잰 시각**이다. 응답의 `timestamp` 를 쓰면
            브로커 시계 오차가 staleness 판정에 섞인다 (spec §4.18).
        """
        market = to_market_code(instrument)
        as_of = datetime.now(UTC)
        rows = await self._get_list("/ticker", group="ticker", params={"markets": market})
        if not rows:
            raise UpbitApiError(f"ticker 응답이 비어 있다: {market}")
        return to_quote(rows[0], instrument, as_of)

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """현재 호가창 스냅샷 (`OrderBookAdapter` 구현 · spec §12.7).

        Args:
            instrument: 대상 종목.

        Returns:
            **30단계** 호가 (실측 — `level` 파라미터 없이 기본 응답이 30단계다).

        Raises:
            UpbitApiError: API 실패 또는 빈 응답.
            UnknownMarketError: 없는 마켓.
            UpbitMappingError: 응답 규격 변경.

        Note:
            `group="orderbook"` 이다 — 업비트는 rate limit 을 엔드포인트 그룹별로 독립
            관리하므로(docs/platform/upbit_api_notes.md §5) 캔들 백필과 예산을 나눠 쓴다. 여기에
            `ticker` 를 쓰면 시세 조회가 호가 샘플링에 밀린다.

            `as_of` 는 **요청 직전**에 잰다. 응답 수신 후에 재면 네트워크 왕복이 호가창
            나이에 섞여, 폴링 지연 분석(§5-1 함정 1)에서 지연을 두 번 세게 된다.
        """
        market = to_market_code(instrument)
        as_of = datetime.now(UTC)
        rows = await self._get_list("/orderbook", group="orderbook", params={"markets": market})
        if not rows:
            raise UpbitApiError(f"orderbook 응답이 비어 있다: {market}")
        return to_orderbook(rows[0], instrument, as_of)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — 코인은 24시간 장이다 (spec §7).

        Args:
            instrument: 대상 종목.

        Returns:
            `MarketSession.ALWAYS_OPEN`.

        Raises:
            UnknownMarketError: 업비트에 없는 마켓.
            UpbitApiError: API 실패.

        Note:
            **심볼 존재 여부를 실제로 확인한다.** 검증 없이 `ALWAYS_OPEN` 을 돌려주면
            없는 종목에 대해 "거래 가능"이라고 답하게 되고, 그것이 조용한 실패다 (spec §7).
            `/ticker` 는 없는 코드에 404 를 주므로(실측) 이것이 가장 싼 검증이다.

            투자경고·거래정지는 담지 못한다 — `MarketStatus` 에 자리가 없다.
            **P2 실주문 전 확장 필수** (docs/platform/upbit_api_notes.md §8).
        """
        market = to_market_code(instrument)
        as_of = datetime.now(UTC)
        await self._get_list("/ticker", group="ticker", params={"markets": market})
        return to_market_status(instrument, as_of)

    async def list_markets(self) -> list[str]:
        """업비트 마켓 코드 전체 목록.

        Returns:
            `["KRW-BTC", "BTC-ETH", ...]`.

        Raises:
            UpbitApiError: API 실패.

        Note:
            `BrokerAdapter` 프로토콜에 없는 **추가 메서드**다. P0-8 instruments 시드와
            WS 구독 전 심볼 검증이 쓴다 — 업비트 WS 는 잘못된 코드를 **조용히 무시**하므로
            (실측) 구독 전 검증이 필요하다.
        """
        rows = await self._get_list("/market/all", group="market", params={"isDetails": "true"})
        return [str(row["market"]) for row in rows if "market" in row]

    async def list_listings(self, quote: str = "") -> list[MarketListing]:
        """마켓 목록을 **이름·거래대금과 함께** 준다 (Phase 5 §5-6 종목 선택).

        Args:
            quote: 기준통화 접두 (`KRW`). 비면 전부.

        Returns:
            거래대금 내림차순 목록.

        Raises:
            UpbitApiError: API 실패.

        Note:
            🔴 `list_markets` 가 코드만 주던 것이 화면에서 문제가 됐다. `KRW-0G`·`KRW-2Z`
            같은 코드를 알파벳순으로 281개 늘어놓으면 **사람이 고를 수 없다** — 무엇이
            비트코인인지도, 무엇이 거래되는 종목인지도 알 수 없다.

            `/market/all` 은 `korean_name` 을 **이미 주고 있었다.** 버리고 코드만 쓰던
            것이 실수였다.

            거래대금은 `/ticker` 를 **한 번에 묶어** 부른다. 종목마다 부르면 281회다.
            업비트 `/ticker` 는 `markets=` 에 쉼표로 여러 개를 받는다.
        """
        rows = await self._get_list("/market/all", group="market", params={"isDetails": "true"})
        pairs = [
            (str(row["market"]), str(row.get("korean_name", "")), str(row.get("english_name", "")))
            for row in rows
            if "market" in row
        ]
        if quote:
            head = f"{quote.upper()}-"
            pairs = [item for item in pairs if item[0].upper().startswith(head)]
        if not pairs:
            return []

        stats: dict[str, dict[str, Any]] = {}
        for start in range(0, len(pairs), TICKER_BATCH):
            chunk = pairs[start : start + TICKER_BATCH]
            got = await self._get_list(
                "/ticker", group="ticker", params={"markets": ",".join(i[0] for i in chunk)}
            )
            for row in got:
                if "market" in row:
                    stats[str(row["market"])] = row

        listings = [
            MarketListing(
                symbol=symbol,
                korean_name=korean,
                english_name=english,
                last_price=_optional_decimal(stats.get(symbol), "trade_price"),
                change_rate=_optional_decimal(stats.get(symbol), "signed_change_rate"),
                turnover_24h=_optional_decimal(stats.get(symbol), "acc_trade_price_24h"),
            )
            for symbol, korean, english in pairs
        ]
        # 거래대금 내림차순 — 사람이 찾는 종목은 대개 위쪽에 있다. 알 수 없으면 맨 뒤로.
        return sorted(
            listings,
            key=lambda item: (item.turnover_24h is None, -(item.turnover_24h or Decimal(0))),
        )

    # ------------------------------------------------------------------
    # 주문 경로 — 전부 차단 (P0-7-6)
    # ------------------------------------------------------------------

    async def get_balance(self) -> Balance:
        """미지원 — 잔고 조회는 인증이 필요하다.

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            잔고는 주문 자체는 아니지만 **인증 키를 요구**한다. P0-7 은 키를 아예 쓰지
            않는 것이 안전 속성이므로 여기서 막는다. 페이퍼 계좌 잔고는 P2-1 의
            `PaperAdapter` 가 가상 원장에서 준다 (spec §4.19).
        """
        raise OrderPathNotAvailableError(
            "UpbitAdapter 는 조회 전용이다 (P0-7). 잔고 조회는 인증 키가 필요하며 "
            "Phase 0~1 에는 열지 않는다. 페이퍼 잔고는 P2-1 PaperAdapter 소관이다"
        )

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """미지원 — Phase 0~1 에는 실주문이 없다.

        Args:
            order: 주문 요청 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            `OrderGateway` 가 이 어댑터를 애초에 반환하지 않으므로(plan D-12) 여기까지
            오는 경로는 없어야 한다. 그럼에도 예외를 두는 것은 **두 번째 방어선**이다 —
            게이트를 우회한 코드가 있다면 조용히 주문되는 대신 여기서 터진다.
        """
        raise OrderPathNotAvailableError(
            f"UpbitAdapter 는 주문을 낼 수 없다 (P0-7 조회 전용). "
            f"요청={order.instrument.symbol} {order.side.value} — "
            "주문 경로는 OrderGateway 가 독점하며 Phase 0~1 에는 열리지 않는다 (절대 규칙 #0)"
        )

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """미지원 — 낼 수 없는 주문은 취소할 수도 없다.

        Args:
            broker_order_id: 브로커 주문 번호 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.
        """
        raise OrderPathNotAvailableError(
            f"UpbitAdapter 는 주문 취소를 지원하지 않는다 (P0-7 조회 전용). "
            f"broker_order_id={broker_order_id}"
        )

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """미지원 — 주문이 없으므로 조회할 상태도 없다.

        Args:
            broker_order_id: 브로커 주문 번호 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            **`OrderStatus.REJECTED` 같은 값을 돌려주지 않는다.** 그럴듯한 상태를 지어내면
            재시도 전 체결 확인(절대 규칙 #6)이 거짓 답을 받게 된다 — 중복 주문의 원인이다.
        """
        raise OrderPathNotAvailableError(
            f"UpbitAdapter 는 주문 상태 조회를 지원하지 않는다 (P0-7 조회 전용). "
            f"broker_order_id={broker_order_id}"
        )

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    async def _get_list(
        self, path: str, *, group: str, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        """배열 응답을 기대하는 GET.

        Args:
            path: 경로.
            group: rate limit 그룹.
            params: 쿼리 파라미터.

        Returns:
            응답 배열.

        Raises:
            UpbitApiError: 배열이 아닌 응답.
        """
        body = await self._client.get_json(path, group=group, params=params)
        if not isinstance(body, list):
            raise UpbitApiError(f"배열 응답을 기대했으나 {type(body).__name__} 을 받았다: {path}")
        return body

    @staticmethod
    def _require_utc(moment: datetime, name: str) -> None:
        """UTC aware 인지 확인한다 (spec §12.3).

        Args:
            moment: 검사할 시각.
            name: 인자 이름 (오류 메시지용).

        Raises:
            ValueError: naive 인 경우.

        Note:
            naive datetime 을 받아 UTC 로 가정하면 호출부의 실수가 조용히 통과한다.
            경계에서 막는 편이 낫다 (절대 규칙 #7, #8).
        """
        if moment.tzinfo is None:
            raise ValueError(f"{name} 은 timezone-aware 여야 한다 (spec §12.3): {moment!r}")
