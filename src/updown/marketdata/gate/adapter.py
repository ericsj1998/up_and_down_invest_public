"""Gate.io 무기한 선물 조회 어댑터 (`OrderBookAdapter` + 파생 조회).

## 🔴 지금은 **조회만** 한다

주문·잔고는 서명이 필요하고, 그 경로는 `execution/gateway.py` 가 독점한다
(절대 규칙 #0). 여기서 `submit_order` 를 부르면 **의도적으로** 예외다 — 조회
어댑터에 주문을 얹으면 "조회만 하는 줄 알았는데 주문도 되는" 물건이 된다.

## 계약 승수를 캐시한다

거래량을 BTC 수량으로 옮기려면 `quanto_multiplier` 가 필요한데, 캔들 조회마다
계약 명세를 함께 받으면 요청이 두 배다. 계약별로 한 번 읽어 들고 있는다.

⚠️ **거래소가 승수를 바꿀 수 있다.** 프로세스가 오래 살면 낡은 값을 쓸 수 있는데,
   그때 거래량이 조용히 틀린다. 지금은 프로세스 수명 동안 캐시하고, 재시작이 갱신
   수단이다 — 이 한계를 여기 적어 둔다 (절대 규칙 #8).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

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
from updown.marketdata.gate.client import GateClient
from updown.marketdata.gate.mapping import (
    CANDLE_LIMIT,
    HISTORY_LIMIT,
    SETTLE,
    GateMappingError,
    earliest_servable,
    interval_of,
    interval_seconds,
    to_candle,
    to_contract,
    to_multiplier,
)
from updown.marketdata.gate.ws import GateCandleStream

_CANDLES = f"/futures/{SETTLE}/candlesticks"
_CONTRACT = f"/futures/{SETTLE}/contracts"
_ORDERBOOK = f"/futures/{SETTLE}/order_book"
_TICKERS = f"/futures/{SETTLE}/tickers"

ORDERBOOK_DEPTH = 50
"""받아 올 호가 단계 수.

⭐ 슬리피지 측정이 **깊이충격**을 보려면 1호가만으로는 부족하다. 업비트 측정이 30단계를
썼고, Gate 는 50까지 한 번에 주므로 그쪽을 쓴다 (`common/costs.sample_slippage` 와 같은
방법을 유지하려면 단계가 넉넉해야 한다).
"""


class GateHistoryTooOldError(GateMappingError):
    """요청 구간이 Gate 의 과거 봉 깊이를 넘었다.

    Note:
        🔴 **따로 뽑는 이유는 처방이 다르기** 때문이다. 다른 오류는 재시도·설정 확인이
        답이지만 이것은 **구간을 좁히거나 다른 출처를 쓰는 것**이 답이다.

        ⚠️ 업비트는 2022-01 까지 적재돼 있고 Gate 는 최근 창만 준다. 4년 백테스트를
        Gate 로 하려는 순간 이 예외가 나며, 그것이 잘못된 계획이라는 신호다.
    """


class OrderPathNotAvailableError(NotImplementedError):
    """Gate 조회 어댑터에는 주문 경로가 없다.

    Note:
        `NotImplementedError` 를 상속하는 이유는 업비트와 같다 — "아직 구현 안 됨"이
        정확한 뜻이고 호출부가 성공으로 오해할 여지가 없다. **조용히 None 을 돌려주지
        않는다** (절대 규칙 #8).

        🔴 업비트보다 이 방어선이 중요하다. 업비트는 키가 없어 물리적으로 주문이
        불가능했지만, Gate 는 **키만 있으면 실제로 주문이 된다.**
    """


class GateAdapter:
    """Gate.io 무기한 선물 — 조회 전용 어댑터.

    Note:
        🔴 `capabilities()` 가 `SHORT`·`LEVERAGE`·`FUNDING` 을 알린다. 상위 계층은
        `if market == 'GATE'` 로 분기하지 않고 이 값을 보고 경로를 켠다 (spec §4.2).

        ⛔ **주문 능력을 알리지 않는다.** 알리면 상위가 주문 경로를 켜고, 그 다음
        실패는 런타임에서 난다. 능력표가 곧 계약이다.
    """

    def __init__(self, client: GateClient) -> None:
        """어댑터를 만든다.

        Args:
            client: 공개 조회 클라이언트. **자격증명을 들지 않는다** — 조회 경로에
                버그가 있어도 주문이 나갈 물리적 수단이 없다는 뜻이다.

        Note:
            ⛔ 클라이언트를 여기서 만들지 않는다. 획득은 `marketdata/provider.py` 가
            독점하며 AST 정적 검사가 강제한다 (절대 규칙 #0).
        """
        self._client = client
        self._multipliers: dict[str, Decimal] = {}

    @property
    def capabilities(self) -> frozenset[Capability]:
        """이 어댑터가 할 수 있는 것.

        Returns:
            능력 집합.

        Note:
            업비트는 `{SPOT, WS, ORDERBOOK}` 이었다. 여기는 **선물**이라 숏·레버리지·
            펀딩이 열린다 — 양방향 플레이북(숏 24%)을 그대로 돌릴 수 있는 이유가 이
            줄이다 (절대 규칙 #10 개정).

            ⛔ `CONDITIONAL_ORDERS` 는 아직 넣지 않는다. Gate 가 스탑 주문을 제공하지만
            **써 보지 않았다** — 능력표에 적으면 상위가 그 경로를 켜고, 서버 다운 중
            손절이 브로커측에서 돈다고 **믿게** 된다. 검증 뒤에 넣는다.
        """
        return frozenset(
            {
                Capability.DERIVATIVES,
                Capability.LEVERAGE,
                Capability.SHORT,
                Capability.FUNDING,
                Capability.ORDERBOOK,
                Capability.WS,
            }
        )

    async def _multiplier(self, contract: str) -> Decimal:
        """계약 승수를 얻는다 (캐시).

        Args:
            contract: `BTC_USDT`.

        Returns:
            `quanto_multiplier`.

        Raises:
            GateMappingError: 명세를 읽을 수 없는 경우. **기본값으로 되돌리지 않는다** —
                모르는 승수를 1 로 채우면 거래량이 1만 배가 된다.
        """
        found = self._multipliers.get(contract)
        if found is not None:
            return found
        payload = await self._client.get_json(f"{_CONTRACT}/{contract}")
        if not isinstance(payload, dict):
            raise GateMappingError(
                f"계약 명세가 객체가 아니다({contract}): {type(payload).__name__}"
            )
        found = to_multiplier(payload)
        self._multipliers[contract] = found
        return found

    async def funding_rate(self, instrument: Instrument) -> Decimal | None:
        """지금 8h 펀딩 요율 (0.8.1 펀딩캡 입력).

        Args:
            instrument: 종목.

        Returns:
            요율 (예: 0.0001 = 0.01%/8h). 못 읽으면 None — 게이트가 잠잔다 (보수 방향).
        """
        payload = await self._client.get_json(f"{_CONTRACT}/{instrument.symbol}")
        if not isinstance(payload, dict):
            return None
        raw = cast("dict[str, object]", payload).get("funding_rate")
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
        """기간 내 캔들을 오름차순으로 반환한다.

        Args:
            instrument: 대상 종목 (`BTC_USDT`).
            timeframe: 시간축.
            start: 조회 시작 (UTC, 포함).
            end: 조회 끝 (UTC, 포함).

        Returns:
            `ts` 오름차순 캔들. 전부 UTC aware 다. 구간에 봉이 없으면 빈 리스트다.

        Raises:
            GateMappingError: naive 시각·역전 구간·응답 형식 오류.
            GateApiError: 호출 실패.

        Note:
            🔴 **거래량이 계약 수로 온다.** 승수를 곱해 BTC 수량으로 옮긴다 — 안 하면
            1만 배다 (`mapping.base_volume`).

            **페이징을 여기서 숨긴다** (프로토콜 계약). Gate 한도는 한 번에 2000봉이라
            15m 으로 20일치다 — 4년 백필은 커서를 돌려야 한다. 호출부가 그것을 알 필요는
            없다.

            ⚠️ **커서가 안 움직이면 멈춘다.** 응답이 비거나 마지막 봉이 진전하지 않으면
            루프를 끝낸다 — 그 방어가 없으면 거래소가 같은 페이지를 계속 줄 때 무한
            루프가 된다. 조용히 도는 무한 루프가 이 프로젝트에서 가장 찾기 어려운 종류다.

            🔴 **`limit` 과 `from`+`to` 를 함께 보낼 수 없다** (실측 2026-08-17):

                400 INVALID_PARAM_VALUE — "`limit` and `from` and `to` cannot be
                present at the same time"

            그래서 `from` + `limit` 으로 **앞으로** 페이징하고 `end` 는 우리가 자른다.
            `to` 를 함께 보내면 페이지 크기를 우리가 못 정해서 몇 번 돌지 알 수 없다.
        """
        for name, moment in (("start", start), ("end", end)):
            if moment.tzinfo is None:
                raise GateMappingError(f"{name} 가 naive 다 (spec §12.3): {moment!r}")
        if start > end:
            raise GateMappingError(
                f"구간이 역전됐다: start={start.isoformat()} > end={end.isoformat()}"
            )

        # 🔴 **사정거리를 먼저 본다.** Gate 는 최근 10,000봉만 준다 — 넘으면 400 이고
        #    상위에서 500 으로 보인다. 500 은 "서버가 깨졌다" 로 읽히는데 실제로는
        #    요청이 범위 밖인 것이다 (절대 규칙 #8).
        floor = earliest_servable(timeframe, datetime.now(UTC))
        if start < floor:
            raise GateHistoryTooOldError(
                f"Gate 는 {timeframe.value} 로 최근 {HISTORY_LIMIT:,}봉만 준다 — "
                f"받을 수 있는 가장 이른 시각은 {floor.isoformat()} 인데 "
                f"{start.isoformat()} 을 요구했다. "
                "4년 백테스트는 적재된 업비트 데이터로 하고, Gate 는 최근 구간·라이브를 맡는다"
            )

        contract = to_contract(instrument)
        multiplier = await self._multiplier(contract)
        interval = interval_of(timeframe)
        step = interval_seconds(timeframe)
        last_second = int(end.timestamp())

        seen: dict[datetime, Candle] = {}
        cursor = int(start.timestamp())
        while cursor <= last_second:
            payload = await self._client.get_json(
                _CANDLES,
                params={
                    "contract": contract,
                    "interval": interval,
                    "from": str(cursor),
                    # ⛔ `to` 를 함께 보내면 400 이다 (`limit` 과 공존 불가). 끝 경계는
                    #    아래에서 우리가 자른다.
                    "limit": str(CANDLE_LIMIT),
                },
            )
            if not isinstance(payload, list):
                raise GateMappingError(f"캔들 응답이 배열이 아니다: {type(payload).__name__}")
            if not payload:
                break
            page = [to_candle(row, instrument, timeframe, multiplier) for row in payload]
            # ⭐ **중복은 dict 가 흡수한다.** Gate 의 `from` 은 포함이라 페이지 경계에서
            #   같은 봉이 두 번 온다. 리스트로 이으면 그 봉이 두 번 세어져 거래량·지표가
            #   조용히 틀린다.
            for candle in page:
                if start <= candle.ts <= end:
                    seen[candle.ts] = candle
            newest = max(candle.ts for candle in page)
            # ⭐ 끝을 넘었으면 그만 — `to` 를 못 보내므로 여기가 유일한 종료 조건이다.
            if newest >= end:
                break
            advanced = int(newest.timestamp()) + step
            if advanced <= cursor:
                # 커서가 안 움직였다 — 같은 페이지를 계속 받는 상황이다. 무한 루프 방지.
                break
            cursor = advanced

        # ⭐ Gate 는 오름차순으로 주지만 **믿지 않고 정렬한다.** 순서가 뒤집힌 시리즈는
        #   지표가 조용히 틀리고(ATR·MA 가 미래를 먼저 본다) 예외가 나지 않는다.
        return [seen[key] for key in sorted(seen)]

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재 시세.

        Args:
            instrument: 대상 종목.

        Returns:
            시세 스냅샷. `as_of` 는 **우리가 잰 시각**이다 (거래소 시계가 아니다).

        Raises:
            GateMappingError: 응답에 계약이 없거나 형식이 다른 경우.
            GateApiError: 호출 실패.

        Note:
            `/tickers` 는 계약을 지정해도 **배열**로 돌려준다. 첫 원소를 그냥 쓰지 않고
            이름을 확인한다 — 규격이 바뀌어 다른 계약이 오면 조용히 남의 가격으로
            거래하게 된다.

            ⭐ `bid`·`ask` 를 함께 담는다. 업비트는 `/ticker` 와 `/orderbook` 이 다른
            그룹이라 `Quote.bid` 가 None 이었는데, Gate 티커는 `highest_bid`·`lowest_ask`
            를 같이 준다 — 호가 요청을 아낄 수 있다.
        """
        contract = to_contract(instrument)
        payload = await self._client.get_json(_TICKERS, params={"contract": contract})
        if not isinstance(payload, list) or not payload:
            raise GateMappingError(f"티커 응답이 비었거나 배열이 아니다({contract}): {payload!r}")
        row = payload[0]
        if str(row.get("contract", "")) != contract:
            raise GateMappingError(
                f"티커가 다른 계약을 돌려줬다: 요청 {contract} · 응답 {row.get('contract')!r} — "
                "남의 가격으로 거래하는 것을 막는다"
            )
        return Quote(
            instrument=instrument,
            last_price=_num(row, "last"),
            bid=_maybe(row, "highest_bid"),
            ask=_maybe(row, "lowest_ask"),
            as_of=datetime.now(UTC),
        )

    async def tickers(self) -> list[dict[str, str]]:
        """**모든 무기한 계약의 티커** — 한 번에 (T18 ②).

        Returns:
            계약별 원문 행들.

        Raises:
            GateMappingError: 응답이 배열이 아닌 경우.
            GateApiError: 호출 실패.

        Note:
            🔴 **종목마다 부르지 않는다.** 20종목이면 요청이 20번이고, 그 사이 값이
            서로 다른 순간의 것이 되어 **순위가 비교가 아니게 된다.** 한 번에 받으면
            모든 행이 같은 순간이다.

            ⭐ 이 한 응답에 순위에 필요한 것이 다 있다:

            ```
            거래대금   volume_24h_quote
            변동성     high_24h · low_24h
            스프레드   highest_bid · lowest_ask
            ```

            ⛔ **판단하지 않는다.** 가중합 점수도 "오늘의 1위" 도 여기서 만들지 않는다 —
            만드는 순간 그것이 새 규칙이 되고, 검증되지 않은 규칙이 화면에서 추천이 된다.
        """
        payload = await self._client.get_json(_TICKERS)
        if not isinstance(payload, list):
            raise GateMappingError(f"티커 응답이 배열이 아니다: {type(payload).__name__}")
        rows = cast("list[dict[str, object]]", payload)
        return [{str(k): str(v) for k, v in row.items()} for row in rows]

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """호가창 스냅샷.

        Args:
            instrument: 대상 종목.

        Returns:
            `levels[0]` 이 1호가인 호가창.

        Raises:
            GateMappingError: 응답 형식이 다르거나 한쪽이 빈 경우.
            GateApiError: 호출 실패.

        Note:
            🔴 **잔량이 계약 수다.** 승수를 곱해 BTC 수량으로 옮긴다 — 슬리피지 측정이
            깊이충격을 재려면 수량 단위가 캔들 거래량과 같아야 한다.

            ⚠️ Gate 는 `bids`·`asks` 를 **따로** 준다. 도메인은 한 행에 묶는 형태라
            (업비트 응답을 따랐다) 인덱스로 재조립한다. 길이가 다르면 짧은 쪽에서
            멈춘다 — 없는 단계를 0 으로 채우면 "잔량 0" 과 구별되지 않는다.
        """
        contract = to_contract(instrument)
        payload = await self._client.get_json(
            _ORDERBOOK, params={"contract": contract, "limit": str(ORDERBOOK_DEPTH)}
        )
        if not isinstance(payload, dict):
            raise GateMappingError(f"호가 응답이 객체가 아니다: {type(payload).__name__}")
        book = payload
        bids = book.get("bids")
        asks = book.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
            raise GateMappingError(
                f"호가 한쪽이 비었다({contract}) — 호가가 비었다는 것 자체가 사건이다"
            )
        multiplier = await self._multiplier(contract)
        rows = cast("list[dict[str, Any]]", bids)
        sells = cast("list[dict[str, Any]]", asks)
        levels = tuple(
            OrderBookLevel(
                bid_price=_num(bid, "p"),
                bid_size=_num(bid, "s") * multiplier,
                ask_price=_num(ask, "p"),
                ask_size=_num(ask, "s") * multiplier,
            )
            for bid, ask in zip(rows, sells, strict=False)
        )
        return OrderBook(instrument=instrument, levels=levels, as_of=datetime.now(UTC))

    async def get_funding_rate(self, instrument: Instrument) -> Decimal:
        """다음 펀딩 비율.

        Args:
            instrument: 대상 종목.

        Returns:
            비율. `0.0001` 이면 8시간마다 0.01% 다.

        Raises:
            GateMappingError: 명세를 읽을 수 없는 경우.
            GateApiError: 호출 실패.

        Note:
            🔴 **업비트 현물에는 없던 비용이다.** 8시간마다(00·08·16 UTC) 부과되므로
            보유가 길면 왕복 비용에 더해진다. 비용표에 칸이 없어서 지금은 이 값을
            읽어만 두고, 모델을 넓힐 때 `funding_pct_per_8h` 로 들어간다.
        """
        contract = to_contract(instrument)
        payload = await self._client.get_json(f"{_CONTRACT}/{contract}")
        if not isinstance(payload, dict):
            raise GateMappingError(f"계약 명세가 객체가 아니다({contract})")
        return _num(payload, "funding_rate")

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 원문.

        Args:
            instrument: 대상 종목.

        Returns:
            `/futures/{settle}/contracts/{contract}` 응답 그대로.

        Raises:
            GateMappingError: 객체가 아닌 경우.
            GateApiError: 호출 실패.

        Note:
            🔴 **유지증거금률·최대 레버리지를 하드코딩하지 않으려고 있다.** 실측
            2026-08-17: 라이브 `maintenance_rate` 0.3% · `leverage_max` 200 인데
            **testnet 은 0.4% · 125** 다. 원장에 박혀 있는 0.5% 도 그래서 틀리다.

            ⚠️ 청산가 계산이 이 값에 달려 있다. 틀린 값으로 계산하면 청산선이 실제보다
            가깝거나 멀다고 믿게 되고, 후자가 계좌를 태운다.
        """
        contract = to_contract(instrument)
        payload = await self._client.get_json(f"{_CONTRACT}/{contract}")
        if not isinstance(payload, dict):
            raise GateMappingError(f"계약 명세가 객체가 아니다({contract})")
        return payload

    def supported_frames(self, frames: Sequence[Timeframe]) -> tuple[Timeframe, ...]:
        """요청한 시간축 중 이 거래소가 캔들을 주는 것만 (순서 유지).

        Args:
            frames: 원하는 시간축들.

        Returns:
            전부 그대로 — Gate 는 10s 부터 전 축을 준다.

        Note:
            Gate 선물 kline 은 10s 부터 전 축을 준다 — 전부 통과. 거래소별 축 차이를
            조립부(walkforward)가 isinstance 로 알지 않게 하는 선언이다 (T63 ②).
        """
        return tuple(frames)

    def candle_stream(
        self, instruments: Sequence[Instrument], timeframe: Timeframe, multiplier: Decimal
    ) -> GateCandleStream:
        """Gate 라이브 캔들 스트림 (T63 §2b — 조립부의 isinstance 분기를 없애는 팩토리).

        Args:
            instruments: 구독할 종목들.
            timeframe: 봉 간격.
            multiplier: 계약 승수.

        Returns:
            아직 열지 않은 스트림.

        Note:
            라이브 URL 로 붙는다 — 조회는 언제나 라이브를 본다 (provider 원칙과 동일).
        """
        return GateCandleStream(instruments, timeframe, multiplier)

    def interval_seconds(self, timeframe: Timeframe) -> int:
        """봉 간격(초) — 수집기가 다음 봉을 기다릴 때 쓴다.

        Args:
            timeframe: 시간축.

        Returns:
            간격(초).
        """
        return interval_seconds(timeframe)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — 무기한 선물은 24시간 장이다.

        Args:
            instrument: 대상 종목.

        Returns:
            늘 열린 상태.

        Raises:
            GateMappingError: GATE 종목이 아닌 경우.

        Note:
            ⚠️ **쉬는 시간이 없다는 것이 "비용 사건이 없다"는 뜻은 아니다.** 펀딩이
            8시간마다(00·08·16 UTC) 정산된다 — 장 상태와는 다른 층이라 여기 안 담고
            `config/costs.yml` GATE 블록에 남겼다.
        """
        to_contract(instrument)
        return MarketStatus(
            instrument=instrument,
            session=MarketSession.ALWAYS_OPEN,
            is_order_allowed=True,
            as_of=datetime.now(UTC),
            # ⭐ 무기한 선물은 개장·폐장이 없다. None 이 "모른다" 가 아니라 **없다** 는
            #   뜻인 자리다 (업비트와 같다).
            next_open=None,
            next_close=None,
        )

    async def get_balance(self) -> Balance:
        """미지원 — 잔고 조회는 인증이 필요하다.

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            🔴 **여기가 키가 처음 필요해지는 지점이다.** 잔고는 주문 자체가 아니지만
            서명을 요구하므로, 조회 클라이언트에 키를 얹으면 "조회만 하는 줄 알았는데
            주문도 되는" 물건이 된다 (업비트와 같은 방침 · spec §8).

            페이크머니 잔고는 testnet 키로 별도 클라이언트가 읽는다.
        """
        raise OrderPathNotAvailableError(
            "GateAdapter 는 조회 전용이다. 잔고는 서명이 필요하며, 키를 든 클라이언트는 "
            "따로 만든다 — 조회 경로에 키를 얹지 않는 것이 주문 차단 방어선이다"
        )

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """미지원 — 주문 경로는 `OrderGateway` 가 독점한다.

        Args:
            order: 주문 요청 (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            🔴 **두 번째 방어선이다.** `OrderGateway` 가 이 어댑터를 애초에 반환하지
            않지만, 게이트를 우회한 코드가 있다면 조용히 주문되는 대신 여기서 터진다
            (절대 규칙 #0).

            ⚠️ Gate 는 **실제로 주문이 되는 거래소**다. 업비트는 키가 없어 물리적으로
            불가능했는데 여기는 키만 있으면 된다 — 그래서 이 방어선이 업비트보다 중요하다.
        """
        raise OrderPathNotAvailableError(
            f"GateAdapter 는 주문을 낼 수 없다 (조회 전용). "
            f"요청={order.instrument.symbol} {order.side.value} — "
            "주문 경로는 OrderGateway 가 독점한다 (절대 규칙 #0)"
        )

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """미지원 — 취소도 주문 경로다.

        Args:
            broker_order_id: 브로커 주문 id (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.
        """
        raise OrderPathNotAvailableError(
            f"GateAdapter 는 주문을 취소할 수 없다 (조회 전용). id={broker_order_id}"
        )

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """미지원 — 주문 조회도 서명이 필요하다.

        Args:
            broker_order_id: 브로커 주문 id (사용되지 않는다).

        Raises:
            OrderPathNotAvailableError: 항상.

        Note:
            ⚠️ 절대 규칙 #6("재시도 전 반드시 체결 여부를 먼저 조회한다")이 이 메서드를
            쓴다. 주문을 열 때 **이것을 먼저** 열어야 한다 — 조회 없이 재시도하면 중복
            주문이 난다.
        """
        raise OrderPathNotAvailableError(
            f"GateAdapter 는 주문 상태를 조회할 수 없다 (조회 전용). id={broker_order_id}"
        )


def _num(payload: dict[str, Any], key: str) -> Decimal:
    """응답 필드를 `Decimal` 로.

    Args:
        payload: 응답 객체.
        key: 필드 이름.

    Returns:
        `Decimal`.

    Raises:
        GateMappingError: 없거나 수로 읽히지 않는 경우.

    Note:
        `float` 을 거치지 않는다 — 가격을 float 로 받으면 정밀도가 조용히 깎인다.
    """
    raw = payload.get(key)
    if raw is None:
        raise GateMappingError(f"{key} 가 없다 — 규격 변경 신호다")
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError) as exc:
        raise GateMappingError(f"{key} 를 수로 읽을 수 없다: {raw!r}") from exc


def _maybe(payload: dict[str, Any], key: str) -> Decimal | None:
    """있으면 `Decimal`, 없으면 None.

    Args:
        payload: 응답 객체.
        key: 필드 이름.

    Returns:
        값이 없거나 빈 문자열이면 None.

    Note:
        ⛔ 0 으로 채우지 않는다. 호가가 0 이라는 것과 "브로커가 안 준다"는 다른 사건이고,
        0 을 넣으면 스프레드가 가격 전체가 된다.
    """
    raw = payload.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    return _num(payload, key)
