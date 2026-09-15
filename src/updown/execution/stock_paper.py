"""주식 **페이퍼** 주문 어댑터 — 토스 시세 위에 가상 체결 (T240 · 2026-09-09).

토스에는 테스트넷이 없다. Gate·Binance 페이퍼가 거래소 테스트넷에 주문을 내는 것과 달리
여기서는 **체결·잔고·조건부 손절을 이 프로세스가 모의**한다. 러너·콘솔이 읽는 모양은
`GatePaperAdapter` 와 같다(계약수·평단·표시가·조건부 목록·마감 손익) — 그래서 러너
코드를 한 줄도 바꾸지 않고 주식 판이 돈다. 주식은 계약 승수 1 · 정수 주 · 배율 1 이다.

## 3중 잠금 (gate_paper 와 같은 사상)

1. 실주문 경로가 **없다** — 어떤 조합의 인자로도 브로커에 주문이 가지 않는다
2. 브로커 이름이 `toss-paper` — 페이크 성적이 실적으로 안 섞인다
3. 능력표가 정한 것만 받는다 — 숏·배율·소수 주는 **즉시 거절**한다 (`config/markets.yml`)

## 체결 모형

- 시장가: 지금 시세에 편도 슬리피지(`config/costs.yml`)를 얹어 즉시 체결.
- 지정가: 대기. 시세가 가격을 건드리면(매수 ≤ · 매도 ≥) **그 가격**에 체결.
- 조건부 손절(`stops_for`): 시세가 트리거 이하로 내려오면 시장가로 전량 청산.
  갭으로 건너뛰면 트리거가 아니라 **지금 시세**에 체결된다 — 세션 `_gap_stop_fill` 과 같은 눈.
- 수수료·거래세는 비용표(`taker_fee_pct` · 없으면 `fee_pct` · 매도에 `tax_pct_sell`)로 뗀다.
  결제 T+n 은 **모형화하지 않는다**
  (T240 결정 · 체결 즉시 현금 · 실주문 전에 넣는다).

## 상태는 DB 에 산다

프로세스가 죽어도 포지션이 사라지면 안 된다 — 사라지면 감사가 "원장에는 있는데 계좌에는
없다" 로 판정해 판이 멈춘다. 시장마다 한 행(`stock_paper_accounts.state` JSONB)에 매 변경마다
통째로 쓴다. 토스 실주문은 당분간 범위 밖이라(사용자 2026-09-09) 이 계좌가 곧 주식 운영 계좌다 —
임시 파일이 아니라 백업이 도는 DB 에 둔다. 저장소는 `StateStore` 뒤에 있어 시험은 파일로 돈다.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from updown.common.cache import TtlCache
from updown.common.costs import MarketCosts, load_cost_table
from updown.common.db.models.ops import StockPaperAccount
from updown.common.domain.candle import Candle
from updown.common.domain.capabilities import Lot, MarketCapabilities, capabilities_of
from updown.common.domain.instrument import Currency, Instrument, Market, Side, Timeframe
from updown.common.domain.market import Balance, MarketStatus, OrderBook, Quote
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import Capability

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_logger = get_logger("execution.stock_paper")

BROKER_NAME = "toss-paper"
STATE_ROOT = Path("logs/stock_paper")
"""`FileStateStore` 의 기본 자리 — 시험·DB 없는 스크립트용. API 는 DB 에 둔다."""
QUOTE_TTL_SECONDS = 1.0
"""같은 초 안의 연속 조회는 한 번만 브로커에 묻는다 (감사·러너·콘솔이 같은 값을 본다)."""
REDUCE_ONLY_KINDS = frozenset({"take_profit", "stop_loss", "close"})


class StockPaperRejectedError(RuntimeError):
    """페이퍼 브로커가 주문을 거절했다 — 능력표·현금·수량 단위 위반.

    실브로커가 거절할 것을 페이퍼가 받아 주면 페이퍼 성적이 실적과 갈린다 (규칙 #8).
    """


class StockQuotes(Protocol):
    """이 어댑터가 조회 어댑터에 요구하는 것 — 구체 클래스를 모른다 (절대 규칙 #0)."""

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """기간 내 캔들.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 구간 시작 (UTC).
            end: 구간 끝 (UTC).

        Returns:
            `ts` 오름차순 봉.
        """
        ...

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재가.

        Args:
            instrument: 종목.

        Returns:
            시세 스냅샷.
        """
        ...

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """호가창.

        Args:
            instrument: 종목.

        Returns:
            호가 단계들.
        """
        ...

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — 캘린더가 답한다.

        Args:
            instrument: 종목.

        Returns:
            세션과 주문 가능 여부.
        """
        ...

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 (승수 · 최소 수량 · 호가단위).

        Args:
            instrument: 종목.

        Returns:
            Gate 명세와 같은 열쇠의 매핑.
        """
        ...


@dataclass
class _Position:
    symbol: str
    size: int
    entry: str
    opened_at: float
    fees: str
    max_size: int
    accum_size: int


@dataclass
class _Order:
    id: str
    symbol: str
    side: str
    kind: str
    order_type: str
    size: int
    price: str | None
    text: str
    status: str
    left: int
    fill_price: str | None
    create_time: float
    finish_time: float | None
    finish_as: str
    reduce_only: bool


@dataclass
class _Stop:
    id: str
    symbol: str
    trigger: str
    size: int
    long: bool
    text: str
    create_time: float


@dataclass
class _Book:
    """한 시장의 페이퍼 계좌."""

    market: str
    currency: str
    cash: str
    seq: int = 0
    positions: dict[str, _Position] = field(default_factory=dict[str, _Position])
    orders: dict[str, _Order] = field(default_factory=dict[str, _Order])
    stops: dict[str, _Stop] = field(default_factory=dict[str, _Stop])
    closes: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    ledger: list[dict[str, str]] = field(default_factory=list[dict[str, str]])


def _now() -> float:
    """체결·주문 시각 — 전송 계층의 벽시계다 (판정은 봉 시각을 쓴다 · 규칙 #5 무관)."""
    return time.time()


def _text(value: Decimal) -> str:
    """Decimal 을 지수 표기 없는 문자열로 — JSON 에 `1E+2` 가 남으면 콘솔·감사가 못 읽는다."""
    return format(value.normalize(), "f") if value else "0"


def _book_from(raw: dict[str, Any]) -> _Book:
    """저장된 JSON 을 계좌로 되살린다 (`_book_payload` 의 역).

    Args:
        raw: `StateStore.load` 가 돌려준 문서.

    Returns:
        계좌. 하위 컬렉션이 빠진 옛 문서도 빈 것으로 읽는다.
    """
    book = _Book(
        market=str(raw["market"]),
        currency=str(raw["currency"]),
        cash=str(raw["cash"]),
        seq=int(raw.get("seq", 0)),
    )
    for key, row in cast("dict[str, dict[str, Any]]", raw.get("positions", {})).items():
        book.positions[key] = _Position(**row)
    for key, row in cast("dict[str, dict[str, Any]]", raw.get("orders", {})).items():
        book.orders[key] = _Order(**row)
    for key, row in cast("dict[str, dict[str, Any]]", raw.get("stops", {})).items():
        book.stops[key] = _Stop(**row)
    book.closes = list(cast("list[dict[str, str]]", raw.get("closes", [])))
    book.ledger = list(cast("list[dict[str, str]]", raw.get("ledger", [])))
    return book


def _book_payload(book: _Book) -> dict[str, Any]:
    """계좌를 저장 문서로 — 매 변경마다 통째로 쓰이므로 이력 꼬리를 자른다.

    Args:
        book: 계좌.

    Returns:
        JSON 직렬화 가능한 dict. `closes` 는 최근 200 · `ledger` 는 최근 400 만 남긴다 —
        행 하나(JSONB)를 매번 다시 쓰기 때문에 무한히 자라면 저장이 느려진다.
    """
    return {
        "market": book.market,
        "currency": book.currency,
        "cash": book.cash,
        "seq": book.seq,
        "positions": {k: asdict(v) for k, v in book.positions.items()},
        "orders": {k: asdict(v) for k, v in book.orders.items()},
        "stops": {k: asdict(v) for k, v in book.stops.items()},
        "closes": book.closes[-200:],
        "ledger": book.ledger[-400:],
    }


class StateStore(Protocol):
    """페이퍼 계좌 상태의 저장소 — 시장 하나에 문서 하나."""

    async def load(self, market: Market) -> dict[str, Any] | None:
        """저장된 상태.

        Args:
            market: 시장.

        Returns:
            없으면 None.
        """
        ...

    async def save(self, market: Market, payload: dict[str, Any]) -> None:
        """상태를 통째로 쓴다.

        Args:
            market: 시장.
            payload: 직렬화된 계좌.
        """
        ...


class FileStateStore:
    """파일 저장소 — 시험과 DB 없는 스크립트용. `<root>/<시장>.json`."""

    def __init__(self, root: Path | None = None) -> None:
        """저장소를 만든다.

        Args:
            root: 디렉토리. None 이면 `logs/stock_paper`.
        """
        self._root = root or STATE_ROOT

    async def load(self, market: Market) -> dict[str, Any] | None:
        """파일이 있으면 읽는다.

        Args:
            market: 시장.

        Returns:
            없으면 None.
        """
        path = self._root / f"{market.value}.json"
        if not path.exists():
            return None
        return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))

    async def save(self, market: Market, payload: dict[str, Any]) -> None:
        """임시 파일에 쓰고 바꿔치기한다 (중간에 죽어도 반쪽 파일이 안 남는다).

        Args:
            market: 시장.
            payload: 직렬화된 계좌.
        """
        path = self._root / f"{market.value}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


class DbStateStore:
    """DB 저장소 — `stock_paper_accounts` 한 행(JSONB). API 가 기동 때 붙인다."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """저장소를 만든다.

        Args:
            factory: DB 세션 팩토리 — 판 저장소와 같은 풀을 쓴다.
        """
        self._factory = factory

    async def load(self, market: Market) -> dict[str, Any] | None:
        """행이 있으면 읽는다.

        Args:
            market: 시장.

        Returns:
            없으면 None.
        """
        async with self._factory() as session:
            found = (
                await session.execute(
                    sa.select(StockPaperAccount.state).where(
                        StockPaperAccount.market == market.value
                    )
                )
            ).scalar_one_or_none()
        return None if found is None else found

    async def save(self, market: Market, payload: dict[str, Any]) -> None:
        """넣거나 덮는다.

        Args:
            market: 시장.
            payload: 직렬화된 계좌.
        """
        async with self._factory() as session:
            statement = insert(StockPaperAccount).values(market=market.value, state=payload)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[StockPaperAccount.market],
                    set_={"state": payload, "updated_at": sa.func.now()},
                )
            )
            await session.commit()


_state_store: StateStore | None = None


def attach_state_store(store: StateStore | None) -> None:
    """프로세스의 페이퍼 상태 저장소를 정한다 — API 기동 훅이 DB 로 붙인다.

    Args:
        store: 저장소. None 이면 뗀다 (종료 · 시험).
    """
    global _state_store
    _state_store = store


def current_state_store() -> StateStore | None:
    """붙어 있는 저장소.

    Returns:
        없으면 None — 게이트가 어댑터를 만들지 않는다 (조용히 파일로 떨어지지 않는다 · 규칙 #8).
    """
    return _state_store


class StockPaperAdapter:
    """주식 페이퍼 — 시세는 진짜, 체결·잔고는 이 프로세스의 모형.

    Note:
        하나의 어댑터가 토스가 다루는 세 시장(KRX·NASDAQ·NYSE)을 다 받는다 — 게이트 캐시가
        조회 어댑터 **종류**로 키를 잡기 때문이다. 시장마다 통화·현금·포지션은 따로 산다.
    """

    def __init__(
        self,
        quotes: StockQuotes,
        *,
        store: StateStore,
        seed_cash: dict[Market, Decimal] | None = None,
    ) -> None:
        """어댑터를 만든다.

        Args:
            quotes: 조회 어댑터. 시세·호가·캔들·장 상태를 위임한다.
            store: 계좌 상태 저장소 (API 는 DB · 시험은 파일).
            seed_cash: 시장별 시작 현금. None 이면 `config/markets.yml` 의 `paper_seed_cash`.
        """
        self._quotes = quotes
        self._store = store
        self._seed = seed_cash
        self._books: dict[Market, _Book] = {}
        self._quote_cache = TtlCache[Quote]("stock_paper.quote", QUOTE_TTL_SECONDS, register=False)
        self._costs: dict[Market, MarketCosts] = {}
        self._caps: dict[Market, MarketCapabilities] = {}

    # ------------------------------------------------------------------
    # 정체
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> frozenset[Capability]:
        """현물 · 호가 · 조건부(모의)."""
        return frozenset({Capability.SPOT, Capability.ORDERBOOK, Capability.CONDITIONAL_ORDERS})

    @property
    def is_testnet(self) -> bool:
        """페이퍼다 — 화면 배지가 읽는다."""
        return True

    @property
    def broker_name(self) -> str:
        """`toss-paper`."""
        return BROKER_NAME

    async def identity(self) -> dict[str, str]:
        """콘솔의 계정 칸 — 페이퍼라는 사실이 모든 칸에 있다.

        Returns:
            user_id·tier·margin_mode·testnet 문자열들.
        """
        return {
            "user_id": "paper",
            "tier": "paper",
            "ip_whitelist": "",
            "margin_mode": "cash",
            "testnet": "true",
        }

    # ------------------------------------------------------------------
    # 조회 위임
    # ------------------------------------------------------------------

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """조회 어댑터에 위임한다.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 구간 시작 (UTC).
            end: 구간 끝 (UTC).

        Returns:
            `ts` 오름차순 봉.
        """
        return await self._quotes.get_candles(instrument, timeframe, start, end)

    async def get_quote(self, instrument: Instrument) -> Quote:
        """조회 어댑터에 위임한다 (1초 캐시).

        Args:
            instrument: 종목.

        Returns:
            시세 스냅샷 — 같은 초 안이면 같은 값.
        """
        return await self._mark(instrument)

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """조회 어댑터에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            호가 단계들.
        """
        return await self._quotes.get_orderbook(instrument)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """조회 어댑터(캘린더)에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            세션과 주문 가능 여부.
        """
        return await self._quotes.get_market_status(instrument)

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 — 승수 1 · 정수 주 · 지금 시세.

        Args:
            instrument: 종목.

        Returns:
            조회 어댑터의 명세에 `mark_price`/`last_price` 를 지금 값으로 덮은 매핑.
        """
        spec = dict(await self._quotes.contract_spec(instrument))
        quote = await self._mark(instrument)
        spec["mark_price"] = str(quote.last_price)
        spec["last_price"] = str(quote.last_price)
        return spec

    async def book_here(self, instrument: Instrument, limit: int = 20) -> dict[str, Any]:
        """호가창을 유동성 판정(`orchestration.liquidity.read_book`)의 모양으로.

        Args:
            instrument: 종목.
            limit: 단계 수.

        Returns:
            `{"bids": [{"p", "s"}…], "asks": […]}`.
        """
        book = await self._quotes.get_orderbook(instrument)
        levels = book.levels[:limit]
        return {
            "bids": [{"p": str(item.bid_price), "s": str(item.bid_size)} for item in levels],
            "asks": [{"p": str(item.ask_price), "s": str(item.ask_size)} for item in levels],
        }

    # ------------------------------------------------------------------
    # 계좌
    # ------------------------------------------------------------------

    async def get_balance(self) -> Balance:
        """페이퍼 잔고 — 시장이 여럿이면 **마지막으로 만진 시장**의 통화로 답한다.

        Returns:
            `broker="toss-paper"` 인 잔고 — 가용 현금과 포지션 매입 원가.

        Note:
            러너는 판을 띄울 때 `get_balance().cash` 를 예산 상한으로 읽는다. 시장을
            아직 하나도 안 만졌으면 `_current_book` 의 기본 시장(`seed_cash` 의 첫 시장 ·
            없으면 NASDAQ)의 시작 현금이다.
        """
        book = await self._current_book()
        await self._settle_all(book)
        locked, reserved = self._locked(book), self._reserved(book)
        return Balance(
            broker=BROKER_NAME,
            currency=Currency(book.currency),
            cash=Decimal(book.cash) - reserved,
            positions_value=locked,
            fx_rate_snapshot=None,
            fetched_at=datetime.now(UTC),
        )

    async def margins(self) -> dict[str, str]:
        """콘솔 계정 칸 — total/available/position_margin/order_margin.

        Returns:
            네 값을 문자열로.
        """
        book = await self._current_book()
        await self._settle_all(book)
        locked, reserved = self._locked(book), self._reserved(book)
        cash = Decimal(book.cash)
        return {
            "total": _text(cash + locked),
            "available": _text(cash - reserved),
            "position_margin": _text(locked),
            "order_margin": _text(reserved),
        }

    async def account_margin(self) -> Decimal:
        """포지션에 묶인 돈 — 주식은 매입 원가 그대로다 (배율 1).

        Returns:
            열린 포지션 매입 원가의 합.
        """
        book = await self._current_book()
        return self._locked(book)

    async def open_positions(self) -> list[dict[str, str]]:
        """모든 시장의 열린 포지션.

        Returns:
            `symbol`/`contract` 를 채운 스냅샷 행들.
        """
        out: list[dict[str, str]] = []
        for market in list(self._books):
            book = self._books[market]
            for symbol in list(book.positions):
                instrument = self._instrument_of(market, symbol)
                snap = await self.position_snapshot(instrument)
                if snap:
                    out.append({**snap, "symbol": symbol, "contract": symbol})
        return out

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        """**계좌가 말하는** 포지션 — 원장이 아니다.

        Args:
            instrument: 종목.

        Returns:
            없으면 `{}`. 있으면 수량·평단·표시가·미실현·배율(1)·증거금(매입 원가)을 문자열로.
        """
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        held = book.positions.get(instrument.symbol)
        if held is None or held.size == 0:
            return {}
        mark = (await self._mark(instrument)).last_price
        entry = Decimal(held.entry)
        return {
            "size": str(held.size),
            "entry_price": _text(entry),
            "mark_price": _text(mark),
            "unrealised_pnl": _text((mark - entry) * held.size),
            "leverage": "1",
            "margin": _text(entry * held.size),
            "liq_price": "",
            "value": _text(mark * held.size),
        }

    async def account_book(self, limit: int = 30) -> list[dict[str, str]]:
        """계정 장부 — 수수료·실현손익 행. 펀딩(`fund`)은 주식에 없다.

        Args:
            limit: 최신순 최대 행 수.

        Returns:
            `type`/`change`/`time`/`text`/`contract` 행들.
        """
        book = await self._current_book()
        return list(reversed(book.ledger))[:limit]

    async def position_closes(
        self, instrument: Instrument, limit: int = 30
    ) -> list[dict[str, str]]:
        """이 종목의 마감 손익 행 — 최신순.

        Args:
            instrument: 종목.
            limit: 최대 행 수.

        Returns:
            Gate `position_closes` 와 같은 열쇠(`pnl`·`pnl_pnl`·`pnl_fee`·`pnl_fund`…)의 행들.
        """
        book = await self._book(instrument.market)
        rows = [row for row in book.closes if row.get("contract") == instrument.symbol]
        return list(reversed(rows))[:limit]

    async def set_leverage(self, instrument: Instrument, leverage: Decimal) -> None:
        """주식은 배율 1 뿐이다 — 다른 값은 거절 (능력표 `leverage_allowed=false`).

        Args:
            instrument: 종목.
            leverage: 원하는 배율.

        Raises:
            StockPaperRejectedError: 1 이 아닌 배율.
        """
        caps = self._capabilities(instrument.market)
        if leverage != 1 and not caps.leverage_allowed:
            raise StockPaperRejectedError(
                f"{instrument.market.value} 는 배율을 쓸 수 없다 — 요청 {leverage} "
                "(config/markets.yml · leverage_allowed=false)"
            )

    # ------------------------------------------------------------------
    # 주문
    # ------------------------------------------------------------------

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """주문을 받는다 — 시장가는 즉시, 지정가는 대기(이미 지났으면 즉시).

        Args:
            order: 주문 요청 — 수량은 정수 주.

        Returns:
            체결이면 FILLED, 대기면 SUBMITTED.

        Raises:
            StockPaperRejectedError: 소수 주 · 숏 · 현금 부족 · 시장 폐장.
        """
        instrument = order.instrument
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        caps = self._capabilities(instrument.market)
        if caps.lot is Lot.INTEGER and order.quantity != order.quantity.to_integral_value():
            raise StockPaperRejectedError(
                f"{instrument.market.value} 는 정수 주만 받는다 — 요청 {order.quantity}"
            )
        size = int(order.quantity)
        if size <= 0:
            raise StockPaperRejectedError(f"수량이 0 이하다 — {order.quantity}")
        held = book.positions.get(instrument.symbol)
        if order.side is Side.SELL and not caps.short_allowed:
            have = held.size if held else 0
            if size > have:
                raise StockPaperRejectedError(
                    f"{instrument.market.value} 는 숏이 없다 — 보유 {have} 주인데 {size} 주 매도"
                )
        status = await self._market_status(instrument)
        if status is not None and not status.is_order_allowed:
            raise StockPaperRejectedError(
                f"{instrument.market.value} 장이 닫혀 있다({status.session.value}) — "
                "주문을 받지 않는다"
            )
        book.seq += 1
        made = _Order(
            id=f"sp{book.seq}",
            symbol=instrument.symbol,
            side=order.side.value,
            kind=order.order_kind.value,
            order_type=order.order_type.value,
            size=size,
            price=None if order.price is None else _text(order.price),
            text=order.idempotency_key,
            status="open",
            left=size,
            fill_price=None,
            create_time=_now(),
            finish_time=None,
            finish_as="",
            reduce_only=order.order_kind.value in REDUCE_ONLY_KINDS,
        )
        book.orders[made.id] = made
        if order.order_type.value == "market" or order.price is None:
            mark = (await self._mark(instrument)).last_price
            price = self._slipped(instrument.market, mark, buy=order.side is Side.BUY)
            self._fill(book, made, price)
        else:
            limit = Decimal(made.price or "0")
            if order.side is Side.BUY:
                need = limit * size
                if need > Decimal(book.cash) - self._reserved(book) + need:
                    del book.orders[made.id]
                    raise StockPaperRejectedError(
                        f"현금 부족 — 필요 {_text(need)} · 가용 "
                        f"{_text(Decimal(book.cash) - self._reserved(book) + need)}"
                    )
            # ⚠️ 시세가 이미 지정가를 지나 있으면 **지금** 체결이다 — 다음 조회까지 대기하면
            #    러너가 "낸 주문이 안 채워진다" 로 읽고, 실브로커라면 즉시 체결됐을 주문이다.
            mark = (await self._mark(instrument)).last_price
            crossed = mark <= limit if order.side is Side.BUY else mark >= limit
            if crossed:
                self._fill(book, made, limit)
        await self._persist(book)
        self._log_order(made)
        return self._result(made)

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """대기 주문을 지운다. 이미 끝난 주문이면 그 상태 그대로 답한다.

        Args:
            broker_order_id: 주문 id.

        Returns:
            취소 뒤 상태.

        Raises:
            StockPaperRejectedError: 모르는 주문.
        """
        for book in self._books.values():
            found = book.orders.get(broker_order_id)
            if found is None:
                continue
            if found.status == "open":
                found.status = "finished"
                found.finish_as = "cancelled"
                found.finish_time = _now()
                await self._persist(book)
            return self._result(found)
        raise StockPaperRejectedError(f"모르는 주문이다 — {broker_order_id}")

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """주문 상태.

        Args:
            broker_order_id: 주문 id.

        Returns:
            상태.

        Raises:
            StockPaperRejectedError: 모르는 주문.
        """
        for book in self._books.values():
            found = book.orders.get(broker_order_id)
            if found is not None:
                return self._status_of(found)
        raise StockPaperRejectedError(f"모르는 주문이다 — {broker_order_id}")

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """대기 중인 지정가.

        Args:
            instrument: 종목.

        Returns:
            Gate `open_orders` 와 같은 열쇠의 행들.
        """
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        return [
            self._order_row(row)
            for row in book.orders.values()
            if row.symbol == instrument.symbol and row.status == "open"
        ]

    async def recent_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """끝난 주문 최신순 — 체결·취소.

        Args:
            instrument: 종목.

        Returns:
            `finish_as`·`fill_price` 를 채운 행들 (최대 40).
        """
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        rows = [
            row
            for row in book.orders.values()
            if row.symbol == instrument.symbol and row.status != "open"
        ]
        rows.sort(key=lambda r: r.finish_time or 0, reverse=True)
        return [{**self._order_row(row), "fill_price": row.fill_price or ""} for row in rows[:40]]

    async def close_position(self, instrument: Instrument, idempotency_key: str) -> OrderResult:
        """전량 시장가 청산.

        Args:
            instrument: 종목.
            idempotency_key: 멱등키 — 주문 `text` 로 남는다.

        Returns:
            체결 결과.

        Raises:
            StockPaperRejectedError: 닫을 포지션이 없다.
        """
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        held = book.positions.get(instrument.symbol)
        if held is None or held.size == 0:
            raise StockPaperRejectedError(f"닫을 포지션이 없다 — {instrument.symbol}")
        book.seq += 1
        made = _Order(
            id=f"sp{book.seq}",
            symbol=instrument.symbol,
            side="sell",
            kind="close",
            order_type="market",
            size=held.size,
            price=None,
            text=idempotency_key,
            status="open",
            left=held.size,
            fill_price=None,
            create_time=_now(),
            finish_time=None,
            finish_as="",
            reduce_only=True,
        )
        book.orders[made.id] = made
        mark = (await self._mark(instrument)).last_price
        self._fill(book, made, self._slipped(instrument.market, mark, buy=False))
        await self._persist(book)
        self._log_order(made)
        return self._result(made)

    # ------------------------------------------------------------------
    # 조건부 손절
    # ------------------------------------------------------------------

    async def stops_for(
        self, instrument: Instrument, trigger: Decimal, *, long: bool
    ) -> str | None:
        """손절이 **그 가격으로** 걸려 있게 만든다 — 없으면 걸고, 다르면 다시 건다.

        Args:
            instrument: 종목.
            trigger: 원장이 정한 손절가.
            long: 보유가 롱인가.

        Returns:
            새로 건 조건부 id. 이미 맞게 걸려 있었으면 None.
        """
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        held = book.positions.get(instrument.symbol)
        size = held.size if held else 0
        mine = [s for s in book.stops.values() if s.symbol == instrument.symbol]
        if any(Decimal(s.trigger) == trigger and s.size == size for s in mine):
            return None
        for stale in mine:
            del book.stops[stale.id]
        book.seq += 1
        made = _Stop(
            id=f"st{book.seq}",
            symbol=instrument.symbol,
            trigger=_text(trigger),
            size=size,
            long=long,
            text="stop",
            create_time=_now(),
        )
        book.stops[made.id] = made
        await self._persist(book)
        return made.id

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸려 있는 조건부.

        Args:
            instrument: 종목.

        Returns:
            Gate `open_stops` 와 같은 열쇠(`id`·`trigger_price`·`size`·`text`…)의 행들.
        """
        book = await self._book(instrument.market)
        await self._settle(book, instrument)
        return [
            {
                "id": row.id,
                "trigger_price": row.trigger,
                "expiration": "",
                "size": str(-row.size if row.long else row.size),
                "reduce_only": "true",
                "text": row.text,
                "create_time": str(row.create_time),
            }
            for row in book.stops.values()
            if row.symbol == instrument.symbol
        ]

    async def cancel_stop(self, stop_id: str) -> None:
        """조건부를 지운다 — 없으면 조용히 (이미 발동했거나 지워졌다).

        Args:
            stop_id: 조건부 id.
        """
        for book in self._books.values():
            if stop_id in book.stops:
                del book.stops[stop_id]
                await self._persist(book)
                return

    # ------------------------------------------------------------------
    # 내부 — 계좌
    # ------------------------------------------------------------------

    async def _book(self, market: Market) -> _Book:
        """시장의 계좌 — 메모리에 없으면 저장소에서, 저장소에도 없으면 새로 연다.

        Args:
            market: 시장.

        Returns:
            계좌. 처음 열 때는 능력표의 결제 통화와 시작 현금으로 만들고 **즉시 저장**한다 —
            개설 사실이 저장소에 남아야 다음 프로세스가 같은 계좌를 잇는다.

        Note:
            마지막으로 만진 시장을 기억해 `get_balance` 같은 시장 인자 없는 호출이 그 시장으로
            답한다 (클래스 docstring — 어댑터 하나가 세 시장을 받는다).
        """
        found = self._books.get(market)
        if found is not None:
            return found
        raw = await self._store.load(market)
        loaded = None if raw is None else _book_from(raw)
        if loaded is None:
            currency = capabilities_of(market).quote_currency  # 능력표 (T269 #6)
            loaded = _Book(
                market=market.value, currency=currency.value, cash=_text(self._seed_for(market))
            )
            await self._store.save(market, _book_payload(loaded))
            _logger.info(
                "stock_paper_account_opened",
                payload={"market": market.value, "cash": loaded.cash, "currency": currency.value},
            )
        self._books[market] = loaded
        self._last_market = market
        return loaded

    _last_market: Market | None = None

    async def _current_book(self) -> _Book:
        """시장 인자가 없는 호출(잔고·장부)이 볼 계좌 — 마지막 시장, 없으면 기본 시장."""
        if self._last_market is not None:
            return await self._book(self._last_market)
        return await self._book(next(iter(self._seed)) if self._seed else Market.NASDAQ)

    def _seed_for(self, market: Market) -> Decimal:
        """시장의 시작 현금 — 생성자 인자가 있으면 그것, 없으면 `config/markets.yml`.

        Args:
            market: 시장.

        Returns:
            시작 현금.

        Raises:
            StockPaperRejectedError: 생성자에 시장별 시작 현금을 넘겼는데 이 시장이 빠진 경우 —
                설정으로 조용히 떨어지지 않는다 (규칙 #8).
        """
        if self._seed is not None:
            found = self._seed.get(market)
            if found is None:
                raise StockPaperRejectedError(f"{market.value} 의 시작 현금이 없다")
            return found
        from updown.common.domain.capabilities import paper_seed_cash

        return paper_seed_cash(market)

    def _capabilities(self, market: Market) -> MarketCapabilities:
        """능력표 — 시장당 한 번만 읽는다 (주문마다 YAML 을 열지 않는다)."""
        found = self._caps.get(market)
        if found is None:
            found = self._caps[market] = capabilities_of(market)
        return found

    def _cost(self, market: Market) -> MarketCosts:
        """비용표 블록 — 시장당 한 번만 읽는다."""
        found = self._costs.get(market)
        if found is None:
            found = self._costs[market] = load_cost_table().for_market(market)
        return found

    async def _persist(self, book: _Book) -> None:
        """계좌를 통째로 저장한다 — 상태를 바꾼 공개 메서드가 끝에서 한 번 부른다."""
        await self._store.save(Market(book.market), _book_payload(book))

    @staticmethod
    def _locked(book: _Book) -> Decimal:
        """포지션에 묶인 돈 — 매입 원가 합 (배율 1 이라 증거금 = 원가)."""
        return sum((Decimal(p.entry) * p.size for p in book.positions.values()), Decimal(0))

    @staticmethod
    def _reserved(book: _Book) -> Decimal:
        """대기 지정가 **매수**가 잡아 둔 현금 — 가용 현금은 `cash - 이 값` 이다.

        Args:
            book: 계좌.

        Returns:
            `open` 상태인 지정가 매수의 `가격 x 남은 수량` 합. 시장가는 즉시 체결돼 현금에서 이미
            빠졌고, 매도는 현금을 쓰지 않으므로 세지 않는다.

        Note:
            상태가 `open` 인 동안만 잡힌다 — `_fill` 이 `finished` 로 바꾸는 순간 예약이 풀리고
            실제 현금 차감이 그 자리를 대신한다. `submit_order` 는 새 주문을 장부에 넣은 **뒤**
            이 값을 보므로, 가용 현금을 계산할 때 자기 몫(`need`)을 도로 더한다.
        """
        return sum(
            (
                Decimal(o.price or "0") * o.left
                for o in book.orders.values()
                if o.status == "open" and o.side == "buy" and o.price is not None
            ),
            Decimal(0),
        )

    def _instrument_of(self, market: Market, symbol: str) -> Instrument:
        """장부의 (시장, 종목 코드) 를 도메인 종목으로 — 저장 문서는 코드만 갖고 있다."""
        from updown.common.domain.instrument import AssetType

        currency = capabilities_of(market).quote_currency  # 능력표 (T269 #6)
        return Instrument(market, symbol, symbol, AssetType.STOCK, currency)

    # ------------------------------------------------------------------
    # 내부 — 시세·체결
    # ------------------------------------------------------------------

    async def _mark(self, instrument: Instrument) -> Quote:
        """표시가 — `QUOTE_TTL_SECONDS` 캐시. 같은 초의 체결·감사·콘솔이 같은 값을 본다."""
        key = f"{instrument.market.value}:{instrument.symbol}"
        return await self._quote_cache.get_or_fetch(key, lambda: self._quotes.get_quote(instrument))

    async def _market_status(self, instrument: Instrument) -> MarketStatus | None:
        """장 상태 — 조회 어댑터가 못 답하면(NotImplementedError) None 이라 폐장 검사를 건너뛴다."""
        try:
            return await self._quotes.get_market_status(instrument)
        except NotImplementedError:
            return None

    def _slipped(self, market: Market, mark: Decimal, *, buy: bool) -> Decimal:
        """시장가 체결가 — 시세에 편도 슬리피지를 불리하게 얹고 호가 눈금으로 내린다."""
        slip = self._cost(market).slippage_pct_one_way
        raw = mark * (1 + slip) if buy else mark * (1 - slip)
        tick = self._tick(market, mark)
        return (raw / tick).quantize(Decimal(1), rounding=ROUND_DOWN) * tick

    def _tick(self, market: Market, price: Decimal) -> Decimal:
        """이 가격대의 호가 눈금 — KRX 는 가격 구간별, 그 외는 비용표 `price_tick`."""
        from updown.common.costs import resolve_tick

        # T250 — 눈금의 단일 출처는 config/costs.yml(`price_tick` · NASDAQ 0.01).
        return resolve_tick(self._cost(market), "", price, krw=market is Market.KRX)

    async def _settle_all(self, book: _Book) -> None:
        """계좌 전체 정산 — 대기 주문·조건부·포지션이 있는 종목만 시세를 본다 (잔고 조회 전)."""
        symbols = {o.symbol for o in book.orders.values() if o.status == "open"}
        symbols |= {s.symbol for s in book.stops.values()}
        symbols |= {p.symbol for p in book.positions.values() if p.size}
        for symbol in symbols:
            await self._settle(book, self._instrument_of(Market(book.market), symbol))

    async def _settle(self, book: _Book, instrument: Instrument) -> None:
        """시세를 한 번 보고 — 대기 지정가·조건부 손절 중 닿은 것을 체결한다."""
        pending = [
            o for o in book.orders.values() if o.symbol == instrument.symbol and o.status == "open"
        ]
        stops = [s for s in book.stops.values() if s.symbol == instrument.symbol]
        if not pending and not stops:
            return
        mark = (await self._mark(instrument)).last_price
        changed = False
        for order in pending:
            price = Decimal(order.price or "0")
            if (order.side == "buy" and mark <= price) or (order.side == "sell" and mark >= price):
                self._fill(book, order, price)
                self._log_order(order)
                changed = True
        for stop in stops:
            trigger = Decimal(stop.trigger)
            hit = mark <= trigger if stop.long else mark >= trigger
            if not hit:
                continue
            held = book.positions.get(instrument.symbol)
            size = min(stop.size, held.size) if held else 0
            del book.stops[stop.id]
            changed = True
            if size <= 0:
                continue
            book.seq += 1
            made = _Order(
                id=f"sp{book.seq}",
                symbol=instrument.symbol,
                side="sell" if stop.long else "buy",
                kind="stop_loss",
                order_type="market",
                size=size,
                price=None,
                text=stop.text,
                status="open",
                left=size,
                fill_price=None,
                create_time=_now(),
                finish_time=None,
                finish_as="",
                reduce_only=True,
            )
            book.orders[made.id] = made
            # ⚠️ 갭이면 트리거가 아니라 **지금 시세**다 — 시가가 손절선 아래로 열리면 그 값.
            fill_at = min(mark, trigger) if stop.long else max(mark, trigger)
            self._fill(book, made, self._slipped(instrument.market, fill_at, buy=not stop.long))
            self._log_order(made)
        if changed:
            await self._persist(book)

    def _fill(self, book: _Book, order: _Order, price: Decimal) -> None:
        """주문 하나를 `price` 에 전량 체결한다 — 포지션·현금·장부·마감 손익을 한 번에 옮긴다.

        Args:
            book: 계좌. 메모리만 바꾼다 — 저장은 부르는 쪽이 `_persist` 로 한다.
            order: 체결할 주문. 부분 체결은 없다 (모형 · 시세가 닿으면 전량).
            price: 체결가. 슬리피지·눈금 처리는 부르는 쪽이 끝냈다 (`_slipped` 또는 지정가).

        Raises:
            StockPaperRejectedError: 보유보다 많이 파는 매도. 주식 현물은 롱 온리라(능력표
                `short_allowed: false` · 규칙 #10) 어떤 경로로 와도 여기서 한 번 더 막는다 —
                `submit_order` 만이 아니라 조건부 발동·`close_position` 도 이 함수를 지난다.

        Note:
            순서가 곧 정합성이다 — 바꾸면 값이 틀어진다.

            1. **거절이 먼저**, 변경은 그 뒤. 매도 초과는 아무것도 바꾸기 전에 던지므로 예외 뒤의
               계좌는 부르기 전과 같다 (반쪽 상태가 저장되지 않는다).
            2. **포지션 → 현금 → 장부.** 평단은 `기존 평단 x 기존 수량 + 이번 대금` 을 새 수량으로
               나눈 값이라 수량을 먼저 늘리면 평단이 틀린다. 정수 주 · 롱 온리(능력표) 전제라
               FIFO 없이 평단 하나로 실현손익을 잰다.
            3. **수수료는 편도 테이커**(`taker` · 없으면 `fee_pct`), 매도에는 `tax_pct_sell` 이
               더해진다. 포지션 `fees` 에 매수·매도 몫을 누적해 마감 때 순손익을 만든다.
            4. **장부 행은 Gate `account_book` 의 열쇠**(`type` = `fee` / `pnl`)를 따른다 —
               `fee` 행은 매수·매도 모두, `pnl` 행은 매도만. 콘솔이 코인과 같은 렌즈로 읽는다.
            5. **마감 행(`closes`)은 수량이 0 이 될 때만** — 부분 청산은 `pnl` 행으로만 남는다.
               `pnl` = 실현손익 - 누적 수수료, `pnl_pnl`/`pnl_fee` 로 분해 (Gate `position_closes`
               와 같은 모양). 그 뒤 포지션을 지우고 **그 종목의 조건부도 전부 지운다** — 팔 것이
               없는 손절이 남아 있으면 다음 시세에 `_settle` 이 빈 매도를 만든다.
            6. **주문 상태는 맨 끝에** `finished` 로. 지정가 매수의 예약금(`_reserved`)은 `open`
               인 동안만 잡히므로, 현금 차감이 끝난 뒤 상태를 바꿔야 예약과 차감이 겹치지 않는다.

            결제 T+n 은 모형화하지 않는다 — 체결 즉시 현금이다 (모듈 docstring · T240 결정).
        """
        market = Market(book.market)
        costs = self._cost(market)
        notional = price * order.size
        fee = notional * costs.taker
        now = _now()
        held = book.positions.get(order.symbol)
        if order.side == "buy":
            if held is None or held.size == 0:
                held = _Position(
                    symbol=order.symbol,
                    size=0,
                    entry="0",
                    opened_at=now,
                    fees="0",
                    max_size=0,
                    accum_size=0,
                )
                book.positions[order.symbol] = held
            total = Decimal(held.entry) * held.size + notional
            held.size += order.size
            held.entry = _text(total / held.size)
            held.fees = _text(Decimal(held.fees) + fee)
            held.max_size = max(held.max_size, held.size)
            held.accum_size += order.size
            book.cash = _text(Decimal(book.cash) - notional - fee)
            book.ledger.append(self._ledger_row("fee", -fee, order, now))
        else:
            if held is None or held.size < order.size:
                raise StockPaperRejectedError(
                    f"보유보다 많이 판다 — {order.symbol} 보유 {held.size if held else 0} · 요청 "
                    f"{order.size}"
                )
            tax = notional * costs.tax_pct_sell
            fee += tax
            realized = (price - Decimal(held.entry)) * order.size
            held.size -= order.size
            held.fees = _text(Decimal(held.fees) + fee)
            book.cash = _text(Decimal(book.cash) + notional - fee)
            book.ledger.append(self._ledger_row("fee", -fee, order, now))
            book.ledger.append(self._ledger_row("pnl", realized, order, now))
            if held.size == 0:
                fees = Decimal(held.fees)
                book.closes.append(
                    {
                        "time": str(now),
                        "contract": order.symbol,
                        "pnl": _text(realized - fees),
                        "pnl_pnl": _text(realized),
                        "pnl_fee": _text(-fees),
                        "pnl_fund": "0",
                        "side": "long",
                        "text": order.text,
                        "max_size": str(held.max_size),
                        "accum_size": str(held.accum_size),
                        "first_open_time": str(held.opened_at),
                        "long_price": held.entry,
                        "short_price": _text(price),
                    }
                )
                del book.positions[order.symbol]
                for stale in [s.id for s in book.stops.values() if s.symbol == order.symbol]:
                    del book.stops[stale]
        order.status = "finished"
        order.finish_as = "filled"
        order.left = 0
        order.fill_price = _text(price)
        order.finish_time = now

    @staticmethod
    def _ledger_row(kind: str, change: Decimal, order: _Order, now: float) -> dict[str, str]:
        """장부 행 — Gate `account_book` 과 같은 열쇠(`type`·`change`·`time`·`text`·`contract`).

        Args:
            kind: `fee` 또는 `pnl`.
            change: 현금 변화. 수수료는 음수로 넣는다.
            order: 원인 주문 — 멱등키(`text`)와 종목이 여기서 온다.
            now: 기록 시각 (epoch 초).

        Returns:
            문자열 값만 든 행 (JSON 저장 · 콘솔 표시 공용).
        """
        return {
            "type": kind,
            "change": _text(change),
            "time": str(now),
            "text": order.text,
            "contract": order.symbol,
        }

    @staticmethod
    def _order_row(row: _Order) -> dict[str, str]:
        """주문을 Gate `open_orders`/`recent_orders` 의 행 모양으로.

        Args:
            row: 주문.

        Returns:
            문자열 행. `size`·`left` 는 Gate 처럼 **매도를 음수**로 적는다 — 러너·콘솔이 부호로
            방향을 읽기 때문이다.
        """
        return {
            "id": row.id,
            "size": str(row.size if row.side == "buy" else -row.size),
            "left": str(row.left if row.side == "buy" else -row.left),
            "price": row.price or "0",
            "fill_price": row.fill_price or "",
            "text": row.text,
            "status": row.status,
            "finish_as": row.finish_as,
            "is_reduce_only": "true" if row.reduce_only else "false",
            "create_time": str(row.create_time),
            "finish_time": "" if row.finish_time is None else str(row.finish_time),
        }

    @staticmethod
    def _status_of(row: _Order) -> OrderStatus:
        """장부의 (`status`, `finish_as`) 쌍을 도메인 상태로.

        Args:
            row: 주문.

        Returns:
            열려 있으면 SUBMITTED, 끝났으면 `finish_as` 대로 FILLED/CANCELLED, 모르는 값은 FAILED.
        """
        if row.status == "open":
            return OrderStatus.SUBMITTED
        if row.finish_as == "filled":
            return OrderStatus.FILLED
        if row.finish_as == "cancelled":
            return OrderStatus.CANCELLED
        return OrderStatus.FAILED

    def _result(self, row: _Order) -> OrderResult:
        """주문을 게이트가 돌려주는 `OrderResult` 로 — 멱등키는 `text` 에 있다.

        Args:
            row: 주문.

        Returns:
            체결 수량은 `size - left`, 평균가는 체결 전이면 None.
        """
        return OrderResult(
            broker_order_id=row.id,
            idempotency_key=row.text,
            status=self._status_of(row),
            filled_quantity=Decimal(row.size - row.left),
            average_price=None if row.fill_price is None else Decimal(row.fill_price),
            ts=datetime.now(UTC),
            reason=None,
        )

    @staticmethod
    def _log_order(row: _Order) -> None:
        """주문의 최종 모습을 로그로 — 접수·체결·조건부 발동 모두 이 한 이벤트로 남는다.

        Args:
            row: 상태가 확정된 주문.
        """
        _logger.info(
            "stock_paper_order",
            payload={
                "id": row.id,
                "symbol": row.symbol,
                "side": row.side,
                "kind": row.kind,
                "type": row.order_type,
                "size": row.size,
                "price": row.price,
                "fill_price": row.fill_price,
                "status": row.status,
                "finish_as": row.finish_as,
                "text": row.text,
            },
        )


__all__ = [
    "BROKER_NAME",
    "DbStateStore",
    "FileStateStore",
    "StateStore",
    "StockPaperAdapter",
    "StockPaperRejectedError",
    "StockQuotes",
    "attach_state_store",
    "current_state_store",
]
