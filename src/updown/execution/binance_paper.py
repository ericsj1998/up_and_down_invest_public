"""바이낸스 **testnet 페이퍼** 주문 어댑터 (T62 P2) — gate_paper 의 미러.

## 3중 잠금 (gate_paper 와 동일 사상)

1. 이 생성자가 **testnet 이 아닌 클라이언트를 거부**한다
2. 자격증명은 `BINANCE_TESTNET_*` 만 읽는다 (`execution/gateway.paper_adapter`)
3. 잔고 브로커 이름이 `binance-testnet` — 페이크 성적이 실적으로 안 섞인다

## 단위 규약 — **계약 = stepSize 코인** (T62 결정)

러너·원장은 Gate 처럼 정수 "계약"으로 산다. 이 어댑터가 유일한 변환 경계다:
주문은 계약 → 코인(x step), 포지션·주문 조회는 코인 → 계약(/step). 이 경계 밖으로
코인 수량이 새면 두 거래소의 사이징 산수가 갈라진다.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Currency, Instrument, Timeframe
from updown.common.domain.market import Balance, MarketStatus, OrderBook, Quote
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus, Side
from updown.common.logging.setup import get_logger
from updown.common.numeric import zero
from updown.marketdata.adapter import Capability
from updown.marketdata.binance.adapter import BinanceAdapter
from updown.marketdata.binance.mapping import price_text, to_symbol
from updown.marketdata.binance.trade_client import BinanceTradeClient
from updown.marketdata.shared_read import forget as forget_shared
from updown.marketdata.shared_read import shared

_logger = get_logger("execution.binance_paper")

BROKER_NAME = "binance-testnet"

#: 바이낸스 주문 상태 → 도메인 상태.
_STATUS: dict[str, OrderStatus] = {
    "NEW": OrderStatus.SUBMITTED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.EXPIRED,
    "REJECTED": OrderStatus.FAILED,
}


class BinanceTestnetOnlyError(RuntimeError):
    """페이퍼 어댑터에 라이브 클라이언트가 들어왔다 — 즉시 거부한다."""


class BinancePaperAdapter:
    """바이낸스 testnet 페이크머니 — 주문 가능 어댑터.

    Note:
        ⚠️ **GTX(post-only) 거부는 예외가 아니라 `EXPIRED` 상태로 온다** (Gate poc 는
        HTTP 거절). 결과 status 를 봐야 하고, 재시도는 부르는 쪽 몫이다.
    """

    def __init__(self, trade: BinanceTradeClient, quotes: BinanceAdapter) -> None:
        """어댑터를 만든다.

        Args:
            trade: **testnet** 서명 클라이언트.
            quotes: 공개 조회 어댑터 (라이브 데이터).

        Raises:
            BinanceTestnetOnlyError: `trade` 가 testnet 이 아닌 경우.
        """
        if not trade.is_testnet:
            raise BinanceTestnetOnlyError(
                "BinancePaperAdapter 에 testnet 이 아닌 클라이언트가 들어왔다 — "
                "페이퍼 어댑터로 라이브에 주문하는 경로는 없다 (spec §12.4)"
            )
        self._trade = trade
        self._quotes = quotes
        self._steps: dict[str, Decimal] = {}

    @property
    def capabilities(self) -> frozenset[Capability]:
        """조회 능력(위임) + 주문 능력.

        Note:
            ✅ `CONDITIONAL_ORDERS` — 2026-08-25 testnet 실측(등록·목록·취소 200).
            ⭐ 바이낸스 조건부는 GTC 라 **24시간 만료가 없다** (Gate 대비 개선점).
        """
        return self._quotes.capabilities | {Capability.SPOT, Capability.CONDITIONAL_ORDERS}

    @property
    def is_testnet(self) -> bool:
        """늘 참이다 — 생성자가 그것만 받는다."""
        return True

    # ── 단위 변환 (계약 = stepSize 코인) ─────────────────────────────

    async def _step(self, instrument: Instrument) -> Decimal:
        """종목의 stepSize(계약 1 = 코인 몇 개) — 심볼당 한 번 명세를 읽고 기억한다."""
        symbol = to_symbol(instrument)
        if symbol not in self._steps:
            spec = await self._trade.contract(symbol)
            self._steps[symbol] = Decimal(str(spec["quanto_multiplier"]))
        return self._steps[symbol]

    async def _to_contracts(self, instrument: Instrument, coins: Decimal) -> Decimal:
        step = await self._step(instrument)
        return (coins / step).quantize(Decimal(1), rounding=ROUND_DOWN)

    async def _tick(self, instrument: Instrument, price: Decimal | None) -> Decimal | None:
        """지정가를 **주문이 나가는 곳(testnet)** 의 호가 단위로 내림한다."""
        if price is None:
            return None
        spec = await self._trade.contract(to_symbol(instrument))
        step = Decimal(str(spec.get("order_price_round", "0.1")))
        if step <= 0:
            return price
        return (price / step).quantize(Decimal(1), rounding=ROUND_DOWN) * step

    # ── 조회 위임 (라이브 데이터) ────────────────────────────────────

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """캔들 — 조회 어댑터(라이브 시장)에 위임한다.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 구간 시작 (UTC).
            end: 구간 끝 (UTC).

        Returns:
            시각 오름차순 캔들. 테스트넷이 아니라 **라이브 시장**의 봉이다 — 판정은 진짜
            가격으로 하고 주문만 페이크머니로 나간다.
        """
        return await self._quotes.get_candles(instrument, timeframe, start, end)

    async def get_quote(self, instrument: Instrument) -> Quote:
        """시세 — 조회 어댑터(라이브)에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            라이브 시장의 현재 시세.
        """
        return await self._quotes.get_quote(instrument)

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """호가 — 조회 어댑터(라이브)에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            라이브 시장의 호가. **주문이 나가는 곳의 호가가 아니다** — 그것은 `book_here`.
        """
        return await self._quotes.get_orderbook(instrument)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — 무기한 선물은 24시간이다.

        Args:
            instrument: 종목.

        Returns:
            조회 어댑터가 답하는 상태. 인터페이스를 맞추기 위해 있다 — 주식 어댑터는 여기서
            휴장·조기마감을 답한다 (C2-4).
        """
        return await self._quotes.get_market_status(instrument)

    async def book_here(self, instrument: Instrument, limit: int = 20) -> dict[str, Any]:
        """**주문이 나가는 곳(testnet)** 의 호가창 — 탈출 가능성 판단용.

        Args:
            instrument: 종목.
            limit: 양쪽 각각 몇 단계까지.

        Returns:
            거래소 원형 호가창(`bids`·`asks`). 잔량 `s` 가 **코인 수량**이다 (Gate 는
            계약 수) — 표시용이라 단위를 맞추지 않는다.
        """
        return await self._trade.order_book(to_symbol(instrument), limit=limit)

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """**주문이 나가는 곳(testnet)** 의 계약 명세.

        Args:
            instrument: 종목.

        Returns:
            거래소 원형 명세 — `quanto_multiplier`(계약 1개 = 코인 몇 개)·`order_price_round`
            (호가 단위) 등. 라이브 명세로 테스트넷 주문을 만들면 거절된다 (Gate ETH 손절
            거절 사고의 교훈).
        """
        return await self._trade.contract(to_symbol(instrument))

    # ── 계정 ─────────────────────────────────────────────────────────

    async def get_balance(self) -> Balance:
        """페이크머니 잔고 — `broker="binance-testnet"` (이름이 곧 경고다).

        Returns:
            `cash` = 가용 잔고 · `positions_value` = 포지션 증거금. 통화는 USD 로 둔다
            (USDT 무기한).

        Note:
            ⭐ **판들이 나눠 쓴다** (2026-08-29 사고). 판 6개가 같은 계좌를 각자 물어
            5분에 266회였다 — 같은 걸음에 겹친 것을 하나로 합친다 (`shared_read`).
        """
        account = await shared(f"{BROKER_NAME}:account", self._trade.get_account)
        return Balance(
            broker=BROKER_NAME,
            currency=Currency.USD,
            cash=Decimal(str(account["available"])),
            positions_value=Decimal(str(account.get("position_margin", "0"))),
            fx_rate_snapshot=None,
            fetched_at=datetime.now(UTC),
        )

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        """거래소가 말하는 포지션 — 원장이 아니다.

        Args:
            instrument: 볼 종목.

        Returns:
            없으면 `{}`. 있으면 계약수·평단·표시가·미실현손익·레버리지·증거금·청산가·명목가치를
            **문자열로** — 표시용이고, 판정에 쓰면 SSoT 가 둘이 된다 (절대 규칙 #4).

        Note:
            `size` 는 **계약 수**(= 코인/step · 부호)다 — Gate 스냅샷과 같은 단위라
            러너의 인수·청산 산수가 그대로 돈다.
        """
        raw = await self._trade.get_position(to_symbol(instrument))
        coins = Decimal(str(raw.get("size", "0") or "0"))
        if coins == 0:
            return {}
        contracts = await self._to_contracts(instrument, coins)
        return {
            "size": str(contracts),
            "entry_price": str(raw.get("entryPrice", "")),
            "mark_price": str(raw.get("markPrice", "")),
            "unrealised_pnl": str(raw.get("unRealizedProfit", "")),
            "leverage": str(raw.get("leverage", "")),
            "margin": str(raw.get("isolatedMargin", "") or raw.get("margin", "")),
            "liq_price": str(raw.get("liquidationPrice", "")),
            "value": str(raw.get("notional", "")),
        }

    async def account_book(self, limit: int = 30) -> list[dict[str, str]]:
        """자금 변동(수입) 이력 — 해석은 부르는 쪽 몫.

        Args:
            limit: 최근 몇 건.

        Returns:
            거래소 원형 행을 값만 문자열로 바꾼 목록 (펀딩·수수료·실현손익이 섞여 있다).
        """
        rows = await self._trade.account_book(limit=limit)
        return [{k: str(v) for k, v in row.items()} for row in rows]

    async def set_leverage(self, instrument: Instrument, leverage: Decimal) -> None:
        """격리 배율을 설정한다.

        Args:
            instrument: 종목.
            leverage: 배율. 값은 결정(decision)이 정하고 여기서는 옮기기만 한다.
        """
        await self._trade.set_leverage(to_symbol(instrument), leverage)

    async def position_closes(
        self, instrument: Instrument, limit: int = 30
    ) -> list[dict[str, str]]:
        """실현 손익 이력 (표시용).

        Args:
            instrument: 종목.
            limit: 최근 몇 건.

        Returns:
            거래소 원형 행을 값만 문자열로 — 손익 귀속은 `attribute_closes` 가 한다.
        """
        rows = await self._trade.position_closes(to_symbol(instrument), limit=limit)
        return [{k: str(v) for k, v in row.items()} for row in rows]

    async def open_positions(self) -> list[dict[str, str]]:
        """계정에 **열려 있는 포지션 전부** — 종목을 몰라도 부를 수 있다.

        Returns:
            `symbol` 을 채운 행들. 없으면 빈 목록.

        Note:
            콘솔이 고아 포지션(판은 지워졌는데 거래소에 남은 것)을 찾는 축이다.
            Gate 쪽과 같은 약속을 지킨다 — `PositionLister` 프로토콜.
        """
        rows = await self._trade.get_positions()
        out: list[dict[str, str]] = []
        for raw in rows:
            if zero(raw.get("size")):
                continue
            row = {name: str(value) for name, value in raw.items()}
            row["symbol"] = str(raw.get("symbol") or raw.get("contract") or "")
            out.append(row)
        return out

    async def account_margin(self) -> Decimal:
        """계정 전체가 잡고 있는 증거금 합 — 옮긴 돈을 잃은 돈으로 세지 않기 위한 값.

        Returns:
            열린 포지션들의 증거금 절댓값 합. 포지션이 없으면 0.

        Note:
            ⭐ **판들이 나눠 쓴다** — 종목별이 아니라 계정 전체를 묻는 조회다
            (실측 5분 357회). 종목별 `position_snapshot` 은 답이 판마다 다르므로
            나눠 쓰지 않는다.
        """
        rows = await shared(f"{BROKER_NAME}:positions", self._trade.get_positions)
        total = Decimal(0)
        for row in rows:
            if zero(row.get("size")):
                continue
            with contextlib.suppress(ArithmeticError, ValueError):
                total += abs(Decimal(str(row.get("margin", "0") or "0")))
        return total

    async def identity(self) -> dict[str, str]:
        """계정 식별 — testnet 여부를 함께 낸다 (페이크 성적 오인 방지).

        Returns:
            `user_id`·`tier`·`ip_whitelist`·`margin_mode`·`testnet` — Gate 와 같은 키.
            테스트넷은 사용자 id 를 주지 않아 브로커 이름을 대신 넣는다.
        """
        account = await shared(f"{BROKER_NAME}:account", self._trade.get_account)
        return {
            "user_id": BROKER_NAME,
            "tier": "",
            "ip_whitelist": "",
            "margin_mode": "multi" if str(account.get("multiAssetsMargin")) == "True" else "single",
            "testnet": "true",
        }

    # ── 주문·조건부 ──────────────────────────────────────────────────

    @staticmethod
    def _order_row(row: dict[str, Any], step: Decimal, *extra: str) -> dict[str, str]:
        """거래소 주문 행 → Gate 호환 평탄 행 (`open_orders` · `recent_orders` 공용).

        Args:
            row: 거래소 원형 주문.
            step: 계약 1개 = 코인 몇 개 — `size`·`left` 를 계약 수로 옮긴다.
            extra: 끝난 주문에만 있는 열 (`fill_price` · `finish_as` · `finish_time`).

        Returns:
            값 전부 문자열인 행.
        """
        coins = Decimal(str(row.get("size", "0") or "0"))
        left = Decimal(str(row.get("left", "0") or "0"))
        out = {
            "id": str(row.get("id", "")),
            "size": str((coins / step).quantize(Decimal(1), rounding=ROUND_DOWN)),
            "left": str((left / step).quantize(Decimal(1), rounding=ROUND_DOWN)),
            "price": str(row.get("price", "")),
            "text": str(row.get("text", "")),
            "status": str(row.get("status", "")),
            "is_reduce_only": str(row.get("is_reduce_only", "")),
            "create_time": str(row.get("create_time", "")),
        }
        for key in extra:
            out[key] = str(row.get(key, ""))
        return out

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """미결 지정가 주문들.

        Args:
            instrument: 종목.

        Returns:
            Gate 와 같은 평탄한 행. `size`·`left` 는 코인이 아니라 **계약 수**로 옮겨서
            대조(`reconcile`)가 거래소를 구분하지 않아도 되게 한다.
        """
        step = await self._step(instrument)
        rows = await self._trade.list_orders(to_symbol(instrument), status="open")
        return [self._order_row(row, step) for row in rows]

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        """조건부(손절) 주문들 — Gate 호환 평탄 형태.

        Args:
            instrument: 종목.

        Returns:
            `id`·`trigger_price`·`size`(계약 수)·`reduce_only`·`text`·`create_time`.
            `expiration` 은 빈 문자열 — 바이낸스 조건부는 GTC 라 만료가 없다 (Gate 와 다르다).
        """
        step = await self._step(instrument)
        rows = await self._trade.list_stops(to_symbol(instrument))
        out: list[dict[str, str]] = []
        for row in rows:
            rule = cast("dict[str, Any]", row.get("trigger") or {})
            order = cast("dict[str, Any]", row.get("initial") or {})
            coins = Decimal(str(order.get("size", "0") or "0"))
            out.append(
                {
                    "id": str(row.get("id", "")),
                    "trigger_price": str(rule.get("price", "")),
                    "expiration": "",  # ⭐ GTC — 만료 없음 (Gate 와 다르다)
                    "size": str((coins / step).quantize(Decimal(1), rounding=ROUND_DOWN)),
                    "reduce_only": str(order.get("reduce_only", "")),
                    "text": str(order.get("text") or row.get("text") or ""),
                    "create_time": str(row.get("create_time", "")),
                }
            )
        return out

    async def recent_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """끝난 주문들 — 무슨 일이 있었나 (미결만 보면 시장가 흔적이 없다).

        Args:
            instrument: 종목.

        Returns:
            최근 40건. `size`·`left` 는 계약 수, `fill_price`·`finish_as` 로 체결/취소/만료를
            가른다.
        """
        step = await self._step(instrument)
        rows = await self._trade.list_orders(to_symbol(instrument), status="finished")
        extra = ("fill_price", "finish_as", "finish_time")
        return [self._order_row(row, step, *extra) for row in rows[:40]]

    async def stops_for(
        self, instrument: Instrument, trigger: Decimal, *, long: bool
    ) -> str | None:
        """브로커측 손절이 **그 가격으로** 걸려 있게 만든다 (없으면 걸고, 다르면 갈아 끼운다).

        Args:
            instrument: 종목.
            trigger: 원하는 손절 트리거가. 호가 단위로 내림해서 비교·등록한다.
            long: 롱 포지션인가 — 트리거 방향(아래/위)을 정한다.

        Returns:
            새로 등록한 조건부 주문 id. 이미 같은 가격으로 걸려 있어 손댈 것이 없으면 None.

        Note:
            거래소에 묻는다 — "걸었다" 를 기억하지 않는다 (Gate 와 같은 원칙). 가격 비교는
            같은 모양(price_text)으로 한다 — 꼬리 0 차이로 매번 갈아 끼우면 그 사이가
            무방비다.
        """
        symbol = to_symbol(instrument)
        ticked = await self._tick(instrument, trigger)
        wanted = price_text(ticked if ticked is not None else trigger)
        existing = await self._trade.list_stops(symbol)
        keep = False
        stale: list[str] = []
        for item in existing:
            rule = cast("dict[str, Any]", item.get("trigger") or {})
            priced = rule.get("price")
            if priced is not None and price_text(Decimal(str(priced))) == wanted and not keep:
                keep = True
                continue
            found = item.get("id")
            if found is not None:
                stale.append(str(found))
        if keep and not stale:
            return None
        # 🔴 등록을 먼저, 취소를 나중에 (2026-09-03 사고 · gate_paper.stops_for 참고) —
        #    취소→등록 사이 재시작이 손절 0개를 만들었다. 이중이 무방비보다 낫다.
        made = (
            None
            if keep
            else await self._trade.place_stop(
                symbol, ticked if ticked is not None else trigger, long=long
            )
        )
        for found_text in stale:
            await self._trade.cancel_stop(found_text, symbol=symbol)
        return made

    async def cancel_stop(self, stop_id: str) -> None:
        """조건부 하나를 거둔다.

        Args:
            stop_id: 거래소 조건부 주문 id (`open_stops` 의 `id`).
        """
        await self._trade.cancel_stop(stop_id)

    async def close_position(self, instrument: Instrument, idempotency_key: str) -> OrderResult:
        """포지션 시장가 전량 청산 — 수량은 거래소가 센다.

        Args:
            instrument: 종목.
            idempotency_key: 멱등키 (절대 규칙 #6). 재시도가 두 번 청산을 내지 않게 한다.

        Returns:
            청산 주문 결과. 체결 수량은 계약 수로 옮겨져 있다.
        """
        placed = await self._trade.close_position(
            to_symbol(instrument), idempotency_key=idempotency_key
        )
        return await self._to_result(instrument, placed, idempotency_key)

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """주문을 낸다 (testnet 페이크머니).

        Args:
            order: 도메인 주문. `quantity` 는 **계약 수**, 방향은 `side`, 지정가는 `price`
                (None 이면 시장가).

        Returns:
            거래소 응답을 옮긴 결과 — 상태·체결 수량(계약 수)·평균가.

        Note:
            🔴 계약 → 코인 변환(x stepSize)은 여기서만 한다. 방향은 부호로 옮긴다.
            ⚠️ post-only(GTX) 거부는 예외가 아니라 결과 `EXPIRED` 로 온다.
        """
        step = await self._step(order.instrument)
        coins = Decimal(int(order.quantity)) * step
        if order.side is Side.SELL:
            coins = -coins
        placed = await self._trade.place_order(
            to_symbol(order.instrument),
            coins,
            idempotency_key=order.idempotency_key,
            price=await self._tick(order.instrument, order.price),
            reduce_only=order.order_kind.value in {"take_profit", "stop_loss", "close"},
            post_only=order.post_only,
        )
        # 🔴 **주문을 냈으면 나눠 쓰던 계좌 값을 버린다** (2026-08-29). 잔고·증거금이
        #    방금 바뀌었는데 2초짜리 옛 값을 다음 판이 받으면, **없는 돈으로 수량을
        #    산정**하고 그 주문은 `INSUFFICIENT_AVAILABLE` 로 거절된다.
        forget_shared(f"{BROKER_NAME}:")
        return await self._to_result(order.instrument, placed, order.idempotency_key)

    async def cancel_order(self, broker_order_id: str, *, symbol: str = "") -> OrderResult:
        """주문 취소 — 이미 체결된 주문의 취소 실패는 정상이며 예외를 삼키지 않는다.

        Args:
            broker_order_id: 거래소 주문 id.
            symbol: BN 형식 심볼 (예: "ETHUSDT"). 어댑터가 발주하지 않은 주문은
                `_symbol_of` 에 없어 심볼 없이는 취소가 불가능하다 (2026-08-26 콘솔
                센티널 익절 취소 실패) — 콘솔 경로가 넘겨준다.

        Returns:
            취소 응답을 옮긴 결과. 이미 체결된 주문이면 거래소 예외가 그대로 올라간다 —
            "취소 못 함" 이 곧 "체결됐다" 는 신호라 삼키지 않는다.
        """
        cancelled = await self._trade.cancel_order(broker_order_id, symbol=symbol)
        symbol = str(cancelled.get("symbol", ""))
        instrument = None
        key = str(cancelled.get("clientOrderId", ""))
        return await self._to_result(instrument, cancelled, key, symbol=symbol)

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """주문 상태 — 재시도 전에 부른다 (절대 규칙 #6).

        Args:
            broker_order_id: 거래소 주문 id.

        Returns:
            도메인 상태. 거래소가 모르는 id 면 `PENDING` — "없다" 를 "취소됐다" 로 읽으면
            재시도가 이중 주문이 된다.
        """
        found = await self._trade.find_order(broker_order_id)
        if found is None:
            return OrderStatus.PENDING
        return self._status_of(found)

    # ── 매퍼 ────────────────────────────────────────────────────────

    def _status_of(self, payload: dict[str, Any]) -> OrderStatus:
        """바이낸스 주문 → 도메인 상태. FILLED 라도 잔량이 있으면 부분 체결이다."""
        raw = str(payload.get("status", ""))
        base = _STATUS.get(raw, OrderStatus.SUBMITTED)
        if base is not OrderStatus.FILLED:
            return base
        orig = Decimal(str(payload.get("origQty", "0") or "0"))
        executed = Decimal(str(payload.get("executedQty", "0") or "0"))
        if executed == 0 and orig > 0:
            return OrderStatus.EXPIRED
        if executed < orig:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.FILLED

    async def _to_result(
        self,
        instrument: Instrument | None,
        payload: dict[str, Any],
        key: str,
        *,
        symbol: str = "",
    ) -> OrderResult:
        """바이낸스 주문 → `OrderResult` — 체결 수량은 **계약 수**로 옮긴다."""
        executed_coins = Decimal(str(payload.get("executedQty", "0") or "0"))
        if instrument is not None:
            filled = await self._to_contracts(instrument, executed_coins)
        else:
            filled = executed_coins  # 심볼 문맥이 없으면 코인 수량 그대로 (표시용)
        price = payload.get("avgPrice")
        average = Decimal(str(price)) if price is not None and str(price) not in {"", "0"} else None
        _logger.info(
            "binance_paper_order_result",
            payload={
                "broker_order_id": str(payload.get("orderId", "")),
                "status": str(payload.get("status", "")),
                "executed": str(executed_coins),
                "avg_price": str(price),
                "symbol": symbol or str(payload.get("symbol", "")),
            },
        )
        return OrderResult(
            broker_order_id=str(payload.get("orderId", "")) or None,
            idempotency_key=key,
            status=self._status_of(payload),
            filled_quantity=filled,
            average_price=average,
            ts=datetime.now(UTC),
            reason=str(payload.get("clientOrderId", "")) or None,
        )
