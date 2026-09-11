"""`TossAdapter` — 국내/해외 주식 **조회 전용** (spec §4.2).

`capabilities = {SPOT, ORDERBOOK}` 다.

> ⚠️ **주문 경로가 없다.** 토스 API 자체에는 주문·조건부주문이 있지만 이 어댑터는 열지
> 않는다. 열리는 시점은 P2-9 이고, 그때도 `OrderGateway` 를 통해서다 (절대 규칙 #0).
>
> 그래서 `CONDITIONAL_ORDERS` 를 **일부러 선언하지 않는다** — 능력으로 선언하면 상위
> 계층이 "브로커측 스탑이 있으니 서버 다운 중에도 손절이 걸린다"(spec §7, §12.6)고
> 믿게 되는데, 이 어댑터는 그 주문을 낼 수단이 없다. 없는 이중화를 있다고 말하는 것이
> 가장 위험한 거짓말이다.

## 이 파일이 흡수하는 브로커 차이 두 가지

**① 시간축.** 토스는 `1m`/`1d` 만 준다. 우리 시간축 5개 중 4개(5m·15m·1h·4h)는 여기서
분봉을 받아 합성한다. 호출부는 그 차이를 모른다 — 그것이 `BrokerAdapter` 추상화의 목적이다.

**② 수정주가.** `adjusted=true` 를 **명시적으로** 보낸다. 액면분할 구간에서 미수정 가격을
쓰면 하루 만에 가격이 1/10 로 떨어지는 봉이 생기고, 그것을 구조물 탐지가 **거대한 FVG·갭**
으로 읽는다. 3년 백테스트가 그 가짜 신호로 오염된다.
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from updown.common.cache import TtlCache
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Market, Timeframe
from updown.common.domain.market import Balance, MarketSession, MarketStatus, OrderBook, Quote
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.common.domain.session import (
    MarketCalendar,
    SessionConfigError,
    Tradability,
    load_calendar,
)
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import Capability
from updown.marketdata.ingest.aggregate import bucket_start, merge_rows
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.stream import CandleStream
from updown.marketdata.toss.client import TossApiError
from updown.marketdata.toss.mapping import (
    DAILY_INTERVAL,
    MAX_CANDLE_COUNT,
    MINUTE_INTERVAL,
    TossMappingError,
    is_native,
    parse_timestamp,
    to_before,
    to_candle,
    to_orderbook,
    to_quote,
    to_row,
    to_symbol,
)

_logger = get_logger("marketdata.toss.adapter")

#: 분봉 간격 — 합성의 원재료 단위.
MINUTE = timedelta(minutes=1)

#: rate limit 그룹 (스펙 "Rate Limits Group").
#:
#: 캔들만 `MARKET_DATA_CHART` 로 분리돼 있다 — 스펙이 "캔들 차트는 호출 부하 특성이 달라
#: 별도 그룹"이라고 밝힌다. 3년치 백필이 현재가·호가 조회 예산을 잡아먹지 않는 이유다.
CHART_GROUP = "MARKET_DATA_CHART"
MARKET_DATA_GROUP = "MARKET_DATA"
STOCK_GROUP = "STOCK"
"""종목 기본 정보 · 유의사항의 요율 그룹 (`/api/v1/stocks`)."""
LIVE_SESSION_TTL_S = 60.0
"""장중 제약(거래정지·VI) 조회 기억 시간 — 걸음마다 물으면 요율만 쓴다 (T259)."""
INFO_BATCH = 200
"""`/api/v1/stocks` · `/api/v1/prices` 가 한 번에 받는 심볼 수 상한."""

#: 미국 주식 호가단위 — $1 이상은 $0.01 (능력표 `tick: broker` · 1달러 미만 종목은 안 다룬다).
US_TICK = Decimal("0.01")
#: 토스가 못 주는 축 — 분봉이 원재료라 초봉은 없다.
_NO_FRAMES = frozenset({Timeframe.S10, Timeframe.S30})

#: 커서 반복 안전 상한의 **여유 배수**.
#:
#: 상한을 고정 상수로 두면 3년치 분봉(1,400페이지 이상)에서 정상 동작이 막힌다. 그렇다고
#: 없애면 커서가 안 도는 버그가 무한 루프가 된다. 그래서 **요청 구간에서 필요한 페이지 수를
#: 계산**하고 여기에 여유를 곱한다 — 구간에 비례해 늘되 폭주는 잡힌다.
PAGE_BUDGET_FACTOR = 3

#: 구간과 무관한 절대 상한. 계산된 예산이 아무리 커도 여기서 멈춘다.
MAX_PAGES_HARD_LIMIT = 20_000

#: 연속 빈 페이지 허용 횟수 — 이만큼 연달아 비면 히스토리 끝으로 본다.
#:
#: 🔴 **빈 페이지가 곧 데이터 끝은 아니다** (실측 2026-08-07). 미국 종목은 `before` 가
#: **정확히 UTC 자정**(=09:00 KST)일 때 양쪽에 데이터가 있는데도 빈 페이지를 준다:
#:
#: | before (AAPL 1m) | 결과 |
#: |---|---|
#: | `2026-07-30T23:59:00Z` | 200건 |
#: | `2026-07-31T00:00:00Z` | **0건** |
#: | `2026-07-31T00:01:00Z` | 1건 |
#:
#: 그런데 우리 백필 창 경계는 전부 UTC 자정이라(앵커가 `2024-07-01T00:00:00Z` 이고 창
#: 길이가 봉의 정확한 배수다) **매 창의 첫 요청이 이 구멍에 떨어진다.** 빈 페이지에서
#: 즉시 멈추면 미국 종목이 통째로 빈다 — 실제로 그렇게 됐다(AAPL 5m 4,702봉 / 정상은
#: 40,000봉 이상).
#:
#: 그래서 빈 페이지를 만나면 커서를 **한 봉 뒤로 밀고 재시도**한다. 커서가 매번
#: 전진하므로 무한 루프가 되지 않고, 이 상한이 두 번째 안전장치다.
MAX_CONSECUTIVE_EMPTY_PAGES = 5


class ResultClient(Protocol):
    """어댑터가 client 에게 바라는 전부 — 직접(`TossClient`)이든 프록시(T275)든 같다.

    토큰·스로틀·재시도는 client 의 책임이고 어댑터는 `result` 안쪽만 받는다.
    프록시(`TossProxyClient`)면 그 책임은 서버의 client 가 진다.
    """

    @property
    def requests(self) -> int:
        """보낸 요청 수 누계 (`RequestCounting`)."""
        ...

    @property
    def budget_used(self) -> int | None:
        """지금 예산 블록에서 이 작업이 쓴 요청 수 — 블록 밖이면 None."""
        ...

    def budget(self, cap: int) -> AbstractContextManager[None]:
        """블록 안 요청 수 상한.

        Args:
            cap: 허용 요청 수. 0 이하면 무제한.

        Returns:
            블록을 닫으면 상한이 풀리는 컨텍스트 매니저.
        """
        ...

    async def get_result(
        self, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> object:
        """GET 하고 `result` 안쪽을 돌려준다.

        Args:
            path: 토스 경로(`/api/v1/candles` 등).
            group: 요율 그룹 이름.
            params: 쿼리.

        Returns:
            `result` 값 — 모양은 끝점마다 다르다.
        """
        ...

    async def aclose(self) -> None:
        """연결을 닫는다."""
        ...


class OrderPathNotAvailableError(NotImplementedError):
    """토스 어댑터에는 주문 경로가 없다 (조회 전용).

    Note:
        `NotImplementedError` 를 상속하는 이유는 `UpbitAdapter` 와 같다 — 호출부가 이것을
        성공으로 오해할 여지가 없다 (spec §7 조용한 실패 금지).
    """


class MarketCalendarRequiredError(NotImplementedError):
    """장 상태를 답하려면 마켓 캘린더가 필요하다 (P2 차단 항목 C2-3·C2-4).

    Note:
        주식은 코인과 달리 `ALWAYS_OPEN` 으로 얼버무릴 수 없다. 휴장일·조기마감(현지
        13:00)·서머타임을 모르면 "지금 거래 가능한가"에 **답할 수 없다**.

        그럴듯한 값을 지어내지 않고 여기서 막는다. 없는 휴장일에 `REGULAR` 을 돌려주면
        상위 계층이 주문을 시도하고, 그것이 조용한 실패다 (절대 규칙 #8).

        P1 백테스트는 `get_candles` 만 쓰므로 이 예외가 경로를 막지 않는다.
    """


def _warning_active(row: Mapping[str, Any], today: date) -> bool:
    """유의사항이 오늘 유효한가 — `startDate <= 오늘 <= endDate`, 없는 쪽은 열린 구간."""
    start, end = row.get("startDate"), row.get("endDate")
    try:
        if isinstance(start, str) and start and date.fromisoformat(start) > today:
            return False
        if isinstance(end, str) and end and date.fromisoformat(end) < today:
            return False
    except ValueError:
        return False
    return True


def _kst_today() -> date:
    """오늘(KST) — 토스 유의사항 날짜의 기준."""
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


class TossAdapter:
    """토스증권 시세 조회 어댑터 (`BrokerAdapter` 구현 중 조회 부분).

    Note:
        `BrokerAdapter` 프로토콜을 **구조적으로** 만족한다 (Protocol 이므로 상속하지
        않는다). 주문 메서드는 시그니처를 지키되 예외를 던진다.
    """

    def __init__(self, client: ResultClient, *, calendar: MarketCalendar | None = None) -> None:
        """어댑터를 만든다.

        Args:
            client: HTTP 클라이언트(직접 또는 프록시). 토큰 발급·스로틀·재시도는
                클라이언트의 책임이다.
            calendar: 장 시간 판정 (시험용). None 이면 처음 필요할 때 `config/market_sessions.yml`.
        """
        self._client = client
        self._calendar = calendar
        self._live = TtlCache[MarketSession | None](
            "toss.live_session", LIVE_SESSION_TTL_S, register=False
        )
        """종목 → (잰 시각, 장중 제약). None 은 "제약 없음" 이고 항목이 없으면 "안 물어봄" 이다."""

    @property
    def capabilities(self) -> frozenset[Capability]:
        """지원 기능 (spec §4.2).

        Returns:
            `{SPOT, ORDERBOOK}`.

        Note:
            `WS` 가 없다 — 토스 Open API 스펙에 WebSocket 엔드포인트가 없다. 실시간
            감시는 폴링 예산을 쓴다는 뜻이고, 그 사실이 상위 계층의 감시 주기 결정에
            들어가야 한다 (spec §4.2).

            `CONDITIONAL_ORDERS` 를 뺀 이유는 모듈 docstring 에 있다.
        """
        return frozenset({Capability.SPOT, Capability.ORDERBOOK})

    @property
    def requests(self) -> int:
        """보낸 HTTP 요청 수 누계 (`RequestCounting` · T253)."""
        return self._client.requests

    @property
    def budget_used(self) -> int | None:
        """지금 예산 블록에서 이 작업이 쓴 요청 수 — client 에 위임. 블록 밖이면 None."""
        return self._client.budget_used

    def budget(self, cap: int) -> AbstractContextManager[None]:
        """요청 상한 블록 — 클라이언트에 그대로 넘긴다 (`RequestCounting` · T253).

        Args:
            cap: 허용 요청 수. 0 이하면 무제한.

        Returns:
            컨텍스트 매니저.
        """
        return self._client.budget(cap)

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
            TossApiError: API 실패.
            UnknownSymbolError: 없는 종목.
            TossMappingError: 응답 규격 변경.

        Note:
            **일봉만 브로커가 그대로 준다.** 나머지는 분봉을 받아 합성한다 — 호출부는
            어느 경로였는지 알 필요가 없다.

            ⚠️ **주식은 장이 열린 시간에만 봉이 있다.** 요청 구간에 휴장일·야간이 섞여
            있으면 반환 개수가 구간 길이에서 기대한 것보다 훨씬 적은데, 그것은 결측이
            아니라 정상이다. 코인(24시간)과 다른 점이라 결측 판정 로직이 이 어댑터의
            결과를 코인과 같은 기준으로 재면 안 된다.
        """
        self._require_utc(start, "start")
        self._require_utc(end, "end")
        if start > end:
            raise ValueError(f"start 가 end 보다 늦다: {start.isoformat()} > {end.isoformat()}")

        symbol = to_symbol(instrument)

        if is_native(timeframe):
            rows = await self._fetch_rows(
                symbol, DAILY_INTERVAL, start, end, step=timedelta(days=1)
            )
            # 🔴 토스는 **내림차순**으로 준다. 계약은 오름차순이므로 반드시 정렬한다 —
            # 합성 경로는 `merge_rows` 가 정렬하지만 이 경로에는 그 단계가 없다.
            candles = sorted(
                (
                    to_candle(row, instrument, timeframe)
                    for row in rows
                    if start <= _row_ts(row) <= end
                ),
                key=lambda bar: bar.ts,
            )
            _logger.info(
                "toss_candles_fetched",
                payload={
                    "symbol": symbol,
                    "timeframe": timeframe.value,
                    "mode": "native",
                    "received": len(rows),
                    "in_range": len(candles),
                },
            )
            return candles

        return await self._synthesize(instrument, symbol, timeframe, start, end)

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재 시세 스냅샷.

        Args:
            instrument: 대상 종목.

        Returns:
            `bid`/`ask` 는 None 이다 — 호가는 별도 엔드포인트다 (mapping 참조).

        Raises:
            TossApiError: API 실패 또는 빈 응답.
            UnknownSymbolError: 없는 종목.

        Note:
            `as_of` 는 **요청 직전에 우리가 잰 시각**이다 (spec §4.18).
        """
        symbol = to_symbol(instrument)
        as_of = datetime.now(UTC)
        result = await self._client.get_result(
            "/api/v1/prices", group=MARKET_DATA_GROUP, params={"symbols": symbol}
        )
        rows = self._as_list(result, "/api/v1/prices")
        if not rows:
            raise TossApiError(f"prices 응답이 비어 있다: {symbol}")
        return to_quote(rows[0], instrument, as_of)

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """현재 호가창 스냅샷 (`OrderBookAdapter` 구현 · spec §12.7).

        Args:
            instrument: 대상 종목.

        Returns:
            1호가부터의 단계들.

        Raises:
            TossApiError: API 실패.
            UnknownSymbolError: 없는 종목.
            TossMappingError: 응답 규격 변경 또는 빈 호가창.

        Note:
            ⚠️ **장 마감 중에는 호가가 비어 매핑이 예외를 던진다.** 코인과 달리 주식은
            "언제든 호가가 있다"가 성립하지 않으므로, 비용 측정(§12.7)을 주식으로 확장할
            때는 **정규장 시간에만 표본을 뜨도록** 스케줄을 짜야 한다. 마감 중 표본을
            섞으면 스프레드가 실제보다 넓게 나온다.
        """
        symbol = to_symbol(instrument)
        as_of = datetime.now(UTC)
        result = self._as_dict(
            await self._client.get_result(
                "/api/v1/orderbook", group=MARKET_DATA_GROUP, params={"symbol": symbol}
            ),
            "/api/v1/orderbook",
        )
        return to_orderbook(result, instrument, as_of)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — **마켓 캘린더가 답한다** (C2-3·C2-4 · T238 로 해소).

        Args:
            instrument: 대상 종목.

        Returns:
            세션과 주문 가능 여부. 캘린더 유효 구간 밖(`UNKNOWN`)은 **불가**로 답한다 —
            그럴듯한 값을 지어내지 않는다 (절대 규칙 #8).

        Raises:
            MarketCalendarRequiredError: 캘린더 설정을 읽을 수 없는 경우.
        """
        now = datetime.now(UTC)
        calendar = self._calendar_or_raise()
        state, _why = calendar.tradability(instrument.market, now)
        next_open, next_close = calendar.next_events(instrument.market, now)
        session = calendar.session_at(instrument.market, now)
        allowed = state is Tradability.OPEN
        if allowed:
            # ⭐ T259 — 달력이 "열림" 이라도 **종목**은 거래정지·정리매매·VI 일 수 있다. 브로커에
            # 묻는다(60초 기억). 못 물으면 달력 답을 그대로 둔다 — 계측 실패가 조회를 막지
            #    않는다.
            live = await self._live_session(to_symbol(instrument))
            if live is not None:
                session, allowed = live, False
        return MarketStatus(
            instrument=instrument,
            session=session,
            is_order_allowed=allowed,
            as_of=now,
            next_open=next_open,
            next_close=next_close,
        )

    # ------------------------------------------------------------------
    # 종목 정보 · 유의사항 (T259 · T260)
    # ------------------------------------------------------------------

    async def stock_info(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """종목 기본 정보 — 상장 시장·상태·발행주식수·거래정지 (`/api/v1/stocks`).

        Args:
            symbols: 토스 심볼들. 200개씩 나눠 부른다.

        Returns:
            스펙 `StockInfo` 행 그대로(`symbol` · `market` · `status` · `securityType` ·
            `sharesOutstanding` · `koreanMarketDetail`…). 모르는 심볼은 빠진다.

        Raises:
            TossApiError: API 실패.
        """
        out: list[dict[str, Any]] = []
        for i in range(0, len(symbols), INFO_BATCH):
            chunk = ",".join(symbols[i : i + INFO_BATCH])
            result = await self._client.get_result(
                "/api/v1/stocks", group=STOCK_GROUP, params={"symbols": chunk}
            )
            out.extend(self._as_list(result, "/api/v1/stocks"))
        return out

    async def last_prices(self, symbols: Sequence[str]) -> dict[str, Decimal]:
        """현재가 묶음 (`/api/v1/prices` · 200개씩).

        Args:
            symbols: 토스 심볼들.

        Returns:
            심볼 → 현재가. 응답에 없는 심볼은 빠진다.

        Raises:
            TossApiError: API 실패.
        """
        out: dict[str, Decimal] = {}
        for i in range(0, len(symbols), INFO_BATCH):
            chunk = ",".join(symbols[i : i + INFO_BATCH])
            result = await self._client.get_result(
                "/api/v1/prices", group=MARKET_DATA_GROUP, params={"symbols": chunk}
            )
            for row in self._as_list(result, "/api/v1/prices"):
                symbol, price = row.get("symbol"), row.get("lastPrice")
                if isinstance(symbol, str) and isinstance(price, str) and price:
                    out[symbol] = Decimal(price)
        return out

    async def warnings_of(self, symbol: str) -> list[dict[str, Any]]:
        """매수 유의사항 — 정리매매·투자경고·VI (`/api/v1/stocks/{symbol}/warnings`).

        Args:
            symbol: 토스 심볼.

        Returns:
            스펙 `StockWarning` 행 그대로(`warningType` · `startDate` · `endDate`…).

        Raises:
            TossApiError: API 실패.
        """
        path = f"/api/v1/stocks/{symbol}/warnings"
        result = await self._client.get_result(path, group=STOCK_GROUP, params={})
        return self._as_list(result, path)

    async def exchange_rate(self, base: str = "USD", quote: str = "KRW") -> dict[str, Any]:
        """환율 (`/api/v1/exchange-rate` · 1분 갱신 · 참고용 표시 환율).

        Args:
            base: 기준 통화.
            quote: 표시 통화.

        Returns:
            스펙 `ExchangeRateResponse`(`rate` · `midRate` · `validFrom` · `validUntil`…).

        Raises:
            TossApiError: API 실패.
        """
        result = await self._client.get_result(
            "/api/v1/exchange-rate",
            group="MARKET_INFO",
            params={"baseCurrency": base, "quoteCurrency": quote},
        )
        return self._as_dict(result, "/api/v1/exchange-rate")

    async def indicator_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        """시장 지표 현재가 — 카탈로그 8종(코스피·코스닥·한국 국채).

        `/api/v1/market-indicators/prices`.

        Args:
            symbols: 카탈로그 심볼들 (`KOSPI` · `KOSDAQ` · `KR_BOND_10Y` …).

        Returns:
            `MarketIndicatorPriceResponse` 행들(`symbol` · `lastPrice` · `timestamp`).

        Raises:
            TossApiError: API 실패(카탈로그 밖 심볼은 400).
        """
        result = await self._client.get_result(
            "/api/v1/market-indicators/prices",
            group="MARKET_INDICATOR",
            params={"symbols": ",".join(symbols)},
        )
        return self._as_list(result, "/api/v1/market-indicators/prices")

    async def market_calendar(self, country: str, day: date | None = None) -> dict[str, Any]:
        """장 운영 달력 — 전일·당일·익일 영업일의 세션 창 (`/api/v1/market-calendar/{US,KR}`).

        Args:
            country: `US` 또는 `KR`.
            day: 기준일(현지 날짜). None 이면 오늘.

        Returns:
            스펙 `UsMarketCalendarResponse` / `KrMarketCalendarResponse`(`previousBusinessDay` ·
            `today` · `nextBusinessDay` · 시각은 KST).

        Raises:
            TossApiError: API 실패.
        """
        path = f"/api/v1/market-calendar/{country}"
        params = {"date": day.isoformat()} if day is not None else {}
        result = await self._client.get_result(path, group="MARKET_INFO", params=params)
        return self._as_dict(result, path)

    @staticmethod
    def live_session_of(
        info: Mapping[str, Any], warnings: Sequence[Mapping[str, Any]], today: date
    ) -> MarketSession | None:
        """종목 정보 + 유의사항 → 장중 제약 (순수 · T259).

        Args:
            info: `StockInfo` 행.
            warnings: `StockWarning` 행들.
            today: 오늘(KST 날짜 — 스펙의 `startDate`/`endDate` 기준).

        Returns:
            상장 상태가 ACTIVE 가 아니거나 거래정지·정리매매면 `HALTED`, 활성 VI 가 있으면 `VI`,
            아니면 None(제약 없음).
        """
        status = info.get("status")
        detail = info.get("koreanMarketDetail")
        detail_map: Mapping[str, Any] = (
            cast("Mapping[str, Any]", detail) if isinstance(detail, Mapping) else {}
        )
        if status not in (None, "ACTIVE"):
            return MarketSession.HALTED
        if detail_map.get("krxTradingSuspended") or detail_map.get("liquidationTrading"):
            return MarketSession.HALTED
        for row in warnings:
            kind = str(row.get("warningType") or "")
            if kind == "LIQUIDATION_TRADING" and _warning_active(row, today):
                return MarketSession.HALTED
            if kind.startswith("VI_") and _warning_active(row, today):
                return MarketSession.VI
        return None

    async def _live_session(self, symbol: str) -> MarketSession | None:
        """장중 실시간 제약 — 60초 기억. 못 물으면 None(제약 모름 = 달력대로).

        Note:
            ⚠️ 유의사항(VI)은 국내 종목에만 있다 — `koreanMarketDetail` 이 없는 종목은 정보만 본다.
        """
        kept = self._live.fresh(symbol)
        if kept is not None:
            return kept[1]
        try:
            rows = await self.stock_info([symbol])
            info: Mapping[str, Any] = rows[0] if rows else {}
            warnings: list[dict[str, Any]] = []
            if isinstance(info.get("koreanMarketDetail"), Mapping):
                warnings = await self.warnings_of(symbol)
            found = self.live_session_of(info, warnings, _kst_today())
        except Exception as exc:
            _logger.warning(
                "toss_live_session_failed", payload={"symbol": symbol, "detail": str(exc)[:120]}
            )
            return None
        self._live.put(symbol, found)
        if found is not None:
            _logger.warning("toss_live_session", payload={"symbol": symbol, "session": found.value})
        return found

    # ------------------------------------------------------------------
    # 라이브 러너 계약 (QuoteAdapter · T240)
    # ------------------------------------------------------------------

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 — 주식은 승수 1 · 정수 주 · 호가단위는 시장 규칙.

        Args:
            instrument: 종목.

        Returns:
            Gate 명세와 같은 열쇠(`quanto_multiplier` · `order_size_min` · `order_price_round` ·
            `mark_price`)로 — 러너·사이징이 시장 이름으로 분기하지 않게.
        """
        quote = await self.get_quote(instrument)
        tick = self._tick_for(instrument, quote.last_price)
        return {
            "name": to_symbol(instrument),
            "quanto_multiplier": "1",
            "order_size_min": 1,
            "order_size_max": 1_000_000,
            "order_price_round": str(tick),
            "mark_price": str(quote.last_price),
            "last_price": str(quote.last_price),
        }

    async def funding_rate(self, instrument: Instrument) -> Decimal | None:  # noqa: ARG002
        """주식에는 펀딩이 없다.

        Args:
            instrument: 종목 (쓰지 않는다 — 계약이 요구하는 자리).

        Returns:
            항상 None.
        """
        return None

    def interval_seconds(self, timeframe: Timeframe) -> int:
        """시간축의 초 길이.

        Args:
            timeframe: 시간축.

        Returns:
            초.
        """
        return int(interval(timeframe).total_seconds())

    def supported_frames(self, frames: Sequence[Timeframe]) -> tuple[Timeframe, ...]:
        """토스가 줄 수 있는 축 — 분봉(1m)에서 합성되는 것 전부 · 초봉은 없다.

        Args:
            frames: 원하는 시간축들.

        Returns:
            그중 토스가 줄 수 있는 것 (순서 유지).
        """
        return tuple(frame for frame in frames if frame not in _NO_FRAMES)

    def candle_stream(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        multiplier: Decimal,  # noqa: ARG002 — 주식은 승수 1 · 계약이 요구하는 자리
    ) -> CandleStream:
        """폴링 스트림 — 토스에는 웹소켓이 없다 (`toss/stream.py`).

        Args:
            instruments: 볼 종목들.
            timeframe: 낼 봉의 축.
            multiplier: 계약 승수 — 주식은 1 이라 쓰지 않는다 (계약이 요구하는 자리).

        Returns:
            캘린더가 열렸을 때만 조회하는 폴링 스트림.
        """
        from updown.marketdata.toss.stream import TossCandleStream

        return TossCandleStream(self, instruments, timeframe, calendar=self._calendar_or_raise())

    def _calendar_or_raise(self) -> MarketCalendar:
        if self._calendar is None:
            try:
                self._calendar = load_calendar()
            except (SessionConfigError, OSError) as exc:
                raise MarketCalendarRequiredError(
                    f"마켓 캘린더를 읽을 수 없다 — {exc}. 장 상태를 지어내지 않는다 (규칙 #8)"
                ) from exc
        return self._calendar

    def _tick_for(self, instrument: Instrument, price: Decimal) -> Decimal:
        if instrument.market is Market.KRX:
            from updown.common.costs import load_cost_table, resolve_tick

            return resolve_tick(
                load_cost_table().for_market(Market.KRX), instrument.symbol, price, krw=True
            )
        return US_TICK

    # ------------------------------------------------------------------
    # 주문 경로 — 전부 차단
    # ------------------------------------------------------------------

    async def get_balance(self) -> Balance:
        """미지원 — 조회 전용 자격증명으로는 계좌에 접근하지 않는다.

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            토스 API 에 `ACCOUNT` 그룹이 있지만 열지 않는다. 이 어댑터가 받는 것은
            `TOSS_MARKETDATA_*` 네임스페이스의 **조회 전용** 자격증명이며, 계좌 경로를
            여는 순간 그 분리가 무의미해진다 (`common/config.py`).
        """
        raise OrderPathNotAvailableError(
            "TossAdapter 는 조회 전용이다. 잔고 조회는 P2-9 에서 OrderGateway 경로로 "
            "열리며, 페이퍼 잔고는 P2-1 PaperAdapter 소관이다"
        )

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """미지원 — Phase 0~1 에는 실주문이 없다.

        Args:
            order: 주문 요청 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.
        """
        raise OrderPathNotAvailableError(
            f"TossAdapter 는 주문을 낼 수 없다 (조회 전용). "
            f"요청={order.instrument.symbol} {order.side.value} — "
            "주문 경로는 OrderGateway 가 독점한다 (절대 규칙 #0)"
        )

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """미지원 — 낼 수 없는 주문은 취소할 수도 없다.

        Args:
            broker_order_id: 브로커 주문 번호 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.
        """
        raise OrderPathNotAvailableError(
            f"TossAdapter 는 주문 취소를 지원하지 않는다 (조회 전용). "
            f"broker_order_id={broker_order_id}"
        )

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """미지원 — 주문이 없으므로 조회할 상태도 없다.

        Args:
            broker_order_id: 브로커 주문 번호 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.
        """
        raise OrderPathNotAvailableError(
            f"TossAdapter 는 주문 상태 조회를 지원하지 않는다 (조회 전용). "
            f"broker_order_id={broker_order_id}"
        )

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        instrument: Instrument,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """분봉을 받아 목표 시간축으로 합성한다.

        Args:
            instrument: 대상 종목.
            symbol: 토스 심볼.
            timeframe: 목표 시간축.
            start: 조회 시작 (UTC, 포함).
            end: 조회 끝 (UTC, 포함).

        Returns:
            합성된 캔들 (오름차순).

        Raises:
            TossApiError: API 실패.

        Note:
            🔴 **경계 봉을 버리지 않기 위해 요청 구간을 앞뒤로 넓힌다.** `start` 가 버킷
            중간이면 그 버킷의 앞부분 분봉이 요청 밖에 있어, 넓히지 않으면 첫 봉의 시가가
            틀린다. 넓힌 뒤 **버킷 시작 기준**으로 다시 자른다.

            불완전 버킷은 버리지 않고 `incomplete` 로 세어 로그에 남긴다 — 주식은 장
            마감 경계에서 정상적으로 발생하므로 **예외가 아니라 관측 대상**이다
            (`aggregate` 모듈 docstring).
        """
        span = interval(timeframe)
        padded_start = bucket_start(start, span)
        padded_end = bucket_start(end, span) + span - MINUTE

        rows = await self._fetch_rows(
            symbol, MINUTE_INTERVAL, padded_start, padded_end, step=MINUTE
        )
        parsed = sorted(
            (to_row(row) for row in rows),
            key=lambda item: item.ts,
        )
        in_window = [row for row in parsed if padded_start <= row.ts <= padded_end]
        if not in_window:
            _logger.info(
                "toss_candles_fetched",
                payload={
                    "symbol": symbol,
                    "timeframe": timeframe.value,
                    "mode": "synthesized",
                    "minutes": 0,
                    "in_range": 0,
                },
            )
            return []

        aggregation = merge_rows(in_window, timeframe, instrument, source_interval=MINUTE)
        candles = [
            candle
            for candle in aggregation.candles
            if bucket_start(start, span) <= candle.ts <= bucket_start(end, span)
        ]
        _logger.info(
            "toss_candles_fetched",
            payload={
                "symbol": symbol,
                "timeframe": timeframe.value,
                "mode": "synthesized",
                "minutes": len(in_window),
                "in_range": len(candles),
                "incomplete_buckets": aggregation.incomplete,
            },
        )
        return candles

    async def _fetch_rows(
        self,
        symbol: str,
        interval_value: str,
        start: datetime,
        end: datetime,
        *,
        step: timedelta,
    ) -> list[dict[str, Any]]:
        """`before` 커서를 과거로 돌려 구간을 채운다.

        Args:
            symbol: 토스 심볼.
            interval_value: `1m` 또는 `1d`.
            start: 구간 시작 (UTC, 포함).
            end: 구간 끝 (UTC, 포함).
            step: 봉 간격 — 페이지 예산 계산에 쓴다.

        Returns:
            원시 캔들 dict 목록 (중복 제거됨, 순서 미보장).

        Raises:
            TossApiError: 커서가 전진하지 않거나 페이지 예산을 넘긴 경우.

        Note:
            🔴 **`before` 는 inclusive 다** (업비트 `to` 는 exclusive). 그래서 두 가지가
            필요하다: ① 서버가 주는 `nextBefore` 를 **그대로** 다음 `before` 로 넘긴다
            (스펙 지시), ② 그럼에도 `ts` 로 **중복 제거**한다 — 첫 페이지의 `before` 는
            우리가 만들고, 경계 봉이 겹칠 수 있다.

            `nextBefore` 가 null 이면 더 과거가 없다는 뜻이므로 즉시 멈춘다. 이것이
            업비트보다 나은 점이다 — 오래된 봉의 `ts` 에서 커서를 유추하지 않아도 된다.
        """
        budget = self._page_budget(start, end, step)
        collected: dict[str, dict[str, Any]] = {}
        # 첫 페이지의 커서는 우리가 만든다. `before` 가 inclusive 이므로 `end` 봉 자체가
        # 포함된다 — 업비트처럼 한 칸 뒤를 가리킬 필요가 없다.
        #
        # 커서를 **datetime 으로** 들고 다니는 이유: 빈 페이지를 만났을 때 한 봉 뒤로
        # 밀어야 하는데(위 `MAX_CONSECUTIVE_EMPTY_PAGES` 주석) 문자열로는 못 민다.
        # 서버가 준 `nextBefore` 를 파싱해 다시 직렬화해도 **같은 순간**이라 안전하다 —
        # 실측에서 `+09:00` 표기와 `+00:00` 표기가 동일한 응답을 준다.
        cursor_at = end
        seen_cursors: set[str] = set()
        empty_streak = 0
        pages = 0

        while pages < budget:
            pages += 1
            params: dict[str, str] = {
                "symbol": symbol,
                "interval": interval_value,
                "count": str(MAX_CANDLE_COUNT),
                # 액면분할 왜곡 방지 — 기본값이 true 여도 **명시적으로** 보낸다.
                # 기본값에 기대면 브로커가 기본값을 바꿀 때 조용히 오염된다.
                "adjusted": "true",
                "before": to_before(cursor_at),
            }
            page = self._as_dict(
                await self._client.get_result("/api/v1/candles", group=CHART_GROUP, params=params),
                "/api/v1/candles",
            )
            rows = self._as_list(page.get("candles"), "/api/v1/candles")
            if not rows:
                # 🔴 빈 페이지가 곧 히스토리 끝은 아니다 — 커서를 한 봉 뒤로 밀고 재시도한다
                # (`MAX_CONSECUTIVE_EMPTY_PAGES` 주석의 실측 근거).
                empty_streak += 1
                cursor_at -= step
                if empty_streak >= MAX_CONSECUTIVE_EMPTY_PAGES or cursor_at < start:
                    break
                continue
            empty_streak = 0

            oldest = min(_row_ts(row) for row in rows)
            for row in rows:
                timestamp = row.get("timestamp")
                if isinstance(timestamp, str):
                    collected[timestamp] = row

            if oldest <= start:
                break

            next_before = page.get("nextBefore")
            if next_before is None:
                # 🔴 `nextBefore=None` 은 "히스토리 끝"이 아니라 **"이 세그먼트 끝"** 이다.
                #
                # 실측(2026-08-07): 미국 종목 분봉은 **거래일마다 끊긴다**.
                #   AAPL  08-01 장중을 걷다 09:01 KST 에서 nextBefore=None
                #   005930 08-01 → 07-31 → 07-30 으로 날짜를 넘어 계속
                #
                # 이것을 끝으로 읽으면 미국 종목이 **하루치만** 적재된다 (AAPL 1h 62봉,
                # 정상은 3,600봉 이상). 받은 것 중 가장 오래된 봉 **직전**으로 커서를
                # 밀어 다음 거래일로 넘어간다.
                #
                # 종료는 보장된다 — `oldest` 가 매번 과거로 가므로 커서가 단조 감소하고,
                # `oldest <= start` 와 페이지 예산이 이중으로 막는다.
                cursor_at = oldest - step
                continue
            if not isinstance(next_before, str):
                raise TossApiError(f"nextBefore 가 문자열이 아니다: {next_before!r}")
            if next_before in seen_cursors:
                # 같은 커서를 다시 받았다. 조용히 돌면 무한 루프다 (spec §7).
                raise TossApiError(
                    f"커서가 전진하지 않는다 (symbol={symbol}, interval={interval_value}, "
                    f"nextBefore={next_before}) — 페이지네이션 규격이 바뀌었을 수 있다. "
                    "docs/platform/toss_api_notes.md 를 재확인하라"
                )
            seen_cursors.add(next_before)
            # 파싱해서 datetime 으로 든다 — 빈 페이지를 만났을 때 밀 수 있어야 한다.
            # 재직렬화해도 같은 순간이라 서버 응답이 달라지지 않는다 (실측).
            cursor_at = parse_timestamp(next_before)
        else:
            raise TossApiError(
                f"커서 반복이 예산({budget}페이지)을 넘었다 — 구간이 너무 넓거나 규격이 "
                f"바뀌었다 (symbol={symbol}, interval={interval_value}, "
                f"start={start.isoformat()}, end={end.isoformat()})"
            )

        return list(collected.values())

    @staticmethod
    def _page_budget(start: datetime, end: datetime, step: timedelta) -> int:
        """구간에서 필요한 페이지 수에 여유를 곱한 상한.

        Args:
            start: 구간 시작.
            end: 구간 끝.
            step: 봉 간격.

        Returns:
            페이지 상한 (최소 2).

        Note:
            주식은 장이 닫힌 시간에 봉이 없으므로 **실제 필요 페이지는 이 계산보다 훨씬
            적다**. 즉 이 값은 넉넉한 상한이지 예상치가 아니다 — 무한 루프만 잡으면 된다.
        """
        bars = max(1, int((end - start) / step) + 1)
        pages = -(-bars // MAX_CANDLE_COUNT)  # 올림
        # 하한이 데이터량과 무관하게 필요하다 — 빈 페이지 재시도와 세그먼트 건너뛰기가
        # 각각 한 페이지를 쓰므로, 좁은 구간에서도 그만큼의 여유가 있어야 한다.
        floor = MAX_CONSECUTIVE_EMPTY_PAGES + 3
        return min(max(floor, pages * PAGE_BUDGET_FACTOR), MAX_PAGES_HARD_LIMIT)

    @staticmethod
    def _as_list(value: object, path: str) -> list[dict[str, Any]]:
        """배열 응답을 기대하는 자리에서 형태를 확인한다.

        Args:
            value: 확인할 값 (`get_result` 는 모양을 확정하지 않는다).
            path: 경로 (오류 메시지용).

        Returns:
            dict 목록.

        Raises:
            TossApiError: 배열이 아니거나 원소가 객체가 아닌 경우.
        """
        if not isinstance(value, list):
            raise TossApiError(f"배열 응답을 기대했으나 {type(value).__name__} 을 받았다: {path}")
        items = cast("list[object]", value)
        for item in items:
            if not isinstance(item, dict):
                raise TossApiError(f"배열 원소가 객체가 아니다: {path} — {item!r}")
        return cast("list[dict[str, Any]]", items)

    @staticmethod
    def _as_dict(value: object, path: str) -> dict[str, Any]:
        """객체 응답을 기대하는 자리에서 형태를 확인한다.

        Args:
            value: 확인할 값.
            path: 경로 (오류 메시지용).

        Returns:
            응답 dict.

        Raises:
            TossApiError: 객체가 아닌 경우.
        """
        if not isinstance(value, dict):
            raise TossApiError(f"객체 응답을 기대했으나 {type(value).__name__} 을 받았다: {path}")
        return cast("dict[str, Any]", value)

    @staticmethod
    def _require_utc(moment: datetime, name: str) -> None:
        """UTC aware 인지 확인한다 (spec §12.3).

        Args:
            moment: 검사할 시각.
            name: 인자 이름 (오류 메시지용).

        Raises:
            ValueError: naive 인 경우.
        """
        if moment.tzinfo is None:
            raise ValueError(f"{name} 은 timezone-aware 여야 한다 (spec §12.3): {moment!r}")


def _row_ts(row: dict[str, Any]) -> datetime:
    """원시 캔들의 시각만 꺼낸다 (커서·범위 판정용).

    Args:
        row: 원시 캔들 dict.

    Returns:
        UTC aware 시각.

    Raises:
        TossMappingError: `timestamp` 부재·형식 오류.

    Note:
        전체 매핑(`to_row`)을 돌리지 않는 이유는 페이지네이션 중 **모든 봉의 가격까지
        Decimal 로 만들 필요가 없기** 때문이다. 3년치 분봉이면 수십만 건이라 무의미한
        비용이 된다.
    """
    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str):
        raise TossMappingError(f"timestamp 가 없거나 문자열이 아니다: {timestamp!r}")
    return parse_timestamp(timestamp)
