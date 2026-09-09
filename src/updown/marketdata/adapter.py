"""브로커 무관 인터페이스 (spec §4.2).

메서드 7종은 spec §4.2 의 의사코드를 그대로 옮긴 것이다. 상위 도메인은 토스/업비트/
페이퍼/백테스트의 차이를 몰라야 하며, 차이는 `capabilities` 선언으로만 드러난다.

> ⚠️ **절대 규칙 #0** — 주문 경로는 이 모듈의 어댑터를 **직접 생성하거나 import 하지
> 않는다.** 반드시 `execution.gateway.OrderGateway` 를 통해 얻는다 (spec §12.4).
> Phase 0~1 에는 PaperAdapter 가 없어 게이트가 **어떤 조건에서도 실주문 어댑터를
> 반환하지 않는다** (plan D-12).
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.market import Balance, MarketStatus, OrderBook, Quote
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.marketdata.stream import CandleStream


class Capability(StrEnum):
    """어댑터가 지원하는 기능 (spec §4.2).

    RiskManager·Execution 은 능력을 조회한 뒤 기능 경로를 활성화한다. 하드코딩된
    `if broker == 'upbit'` 분기가 상위 계층에 나타나면 이 추상화가 샌 것이다.

    Attributes:
        SPOT: 현물 거래.
        DERIVATIVES: 파생 상품.
        LEVERAGE: 레버리지.
        SHORT: 공매도.
        WS: WebSocket 실시간 시세. 있으면 폴링 예산을 소모하지 않는다 (spec §4.2).
        CONDITIONAL_ORDERS: 브로커측 조건부(스탑) 주문. **서버 다운 중 손절가 도달**
            시나리오의 이중화 수단이다 (spec §7, §12.6).
        FUNDING: 펀딩비.
        ORDERBOOK: 호가창 조회 (`OrderBookAdapter`). 있으면 **슬리피지를 가정하지 않고
            측정**할 수 있다 (spec §12.7 비용 테이블). 없으면 비용 모델의 슬리피지 항이
            가정값으로 남는다.

    Note:
        업비트는 `{SPOT, WS, ORDERBOOK}` 다 — 국내 규제상 원화 거래소는 마진/레버리지/
        선물을 제공하지 않는다 (spec §4.2, §12.8).
    """

    SPOT = "spot"
    DERIVATIVES = "derivatives"
    LEVERAGE = "leverage"
    SHORT = "short"
    WS = "ws"
    CONDITIONAL_ORDERS = "conditional_orders"
    FUNDING = "funding"
    ORDERBOOK = "orderbook"


@runtime_checkable
class BrokerAdapter(Protocol):
    """브로커 무관 시세·주문 인터페이스 (spec §4.2 메서드 7종).

    구현체: `TossAdapter`(국내/미국 주식), `UpbitAdapter`(코인 현물 — P0-7),
    `PaperAdapter`(실시세 + 가상 체결 — P2-1), `BacktestAdapter`(과거 재생 — P1-8).

    Note:
        **이 레이어의 책임**: rate limit 관리, 지수 백오프 재시도, 응답 캐싱,
        호가단위 라운딩(spec §12.2), 심볼 매핑. 상위 도메인이 이것들을 알 필요가 없다.

        메서드가 전부 `async` 인 이유: 런타임 전체가 asyncio 다 (FastAPI, httpx async,
        `redis.asyncio`, psycopg async, AsyncIOScheduler). 과거 데이터를 재생하는
        `BacktestAdapter` 처럼 I/O 가 없는 구현도 같은 시그니처를 지켜야 전략 코드가
        백테스트와 실거래에서 동일하게 돈다 (원칙 P3).
    """

    @property
    def capabilities(self) -> frozenset[Capability]:
        """이 어댑터가 지원하는 기능 집합 (spec §4.2)."""
        ...

    async def get_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """기간 내 캔들을 오름차순으로 반환한다.

        Args:
            instrument: 대상 종목.
            timeframe: 시간축.
            start: 조회 시작 (UTC, 포함).
            end: 조회 끝 (UTC, 포함).

        Returns:
            `ts` 오름차순 캔들. 전부 UTC aware 다 (spec §12.3).

        Note:
            브로커의 1회 최대 개수를 넘는 구간은 어댑터가 커서를 돌려 채운다.
            호출부가 페이지네이션을 알 필요는 없다.
        """
        ...

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재 시세 스냅샷을 반환한다.

        Args:
            instrument: 대상 종목.

        Returns:
            조회 시각(`as_of`)이 포함된 시세. staleness 판정의 근거다 (spec §4.18).
        """
        ...

    async def get_balance(self) -> Balance:
        """계좌 잔고를 반환한다.

        Returns:
            잔고 스냅샷. 페이퍼 계좌는 `broker='paper'` 로 같은 스키마를 쓴다
            (spec §4.19).
        """
        ...

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """주문을 제출한다.

        Args:
            order: 멱등키가 포함된 주문 요청.

        Returns:
            제출 결과.

        Note:
            타임아웃·5xx 후 재시도할 때는 **반드시 체결 여부를 먼저 조회**한다
            (spec §7, 절대 규칙 #6). 멱등키 없이 재시도하면 중복 주문이다.
        """
        ...

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """주문을 취소한다.

        Args:
            broker_order_id: 브로커 주문 번호.

        Returns:
            취소 결과.
        """
        ...

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """주문의 현재 상태를 조회한다.

        Args:
            broker_order_id: 브로커 주문 번호.

        Returns:
            주문 상태. 정합성 루프(spec §4.10)와 재시도 전 체결 확인에 쓴다.
        """
        ...

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 운영 상태를 조회한다 (정규장/VI/사이드카/휴장).

        Args:
            instrument: 대상 종목.

        Returns:
            장 상태. 코인은 24시간 장이므로 `MarketSession.ALWAYS_OPEN` 이다 (spec §7).
        """
        ...


@runtime_checkable
class QuoteAdapter(Protocol):
    """라이브 러너·조립부가 **조회 어댑터**에 요구하는 계약 (T63 §2b).

    구현: `GateAdapter` · `BinanceAdapter`. 새 거래소는 이 계약을 지키고 provider 에
    등록하면 러너에 꽂힌다 — `GateAdapter | BinanceAdapter` 유니온을 소멸시키는
    선언이다.

    Note:
        `BrokerAdapter`(§4.2 주문 포함 7종)와 별개다 — 이 계약은 조회 전용이라
        자격증명이 없고, 절대 규칙 #0 의 게이트 독점 대상이 아니다.
    """

    async def get_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """기간 내 캔들.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 구간 시작 (UTC aware).
            end: 구간 끝 (UTC aware).

        Returns:
            `ts` 오름차순 · UTC aware 캔들. 페이징은 구현이 하고 호출자는 모른다.
        """
        ...

    async def get_quote(self, instrument: Instrument) -> Quote:
        """현재가 스냅샷.

        Args:
            instrument: 종목.

        Returns:
            시세. `as_of` 는 우리가 받은 시각 — staleness 판단의 기준이다.
        """
        ...

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 — 승수·주문 한도 등 (Gate 호환 키).

        Args:
            instrument: 종목.

        Returns:
            거래소 원문에 `quanto_multiplier`·`order_size_min`·`order_price_round` 를 얹은 dict.
            러너의 정수 계약 사이징이 거래소를 구분하지 않게 하는 약속이다.
        """
        ...

    async def funding_rate(self, instrument: Instrument) -> Decimal | None:
        """지금 8h 펀딩 요율.

        Args:
            instrument: 종목.

        Returns:
            요율. 못 읽으면 None — 펀딩캡 게이트가 잠잔다 (보수 방향).
        """
        ...

    def interval_seconds(self, timeframe: Timeframe) -> int:
        """시간축의 초 길이 (피드 빌더가 쓴다).

        Args:
            timeframe: 시간축.

        Returns:
            초. 피드 빌더가 다음 봉 마감 시각을 계산한다.
        """
        ...

    def supported_frames(self, frames: Sequence[Timeframe]) -> tuple[Timeframe, ...]:
        """요청한 시간축 중 이 거래소가 캔들을 주는 것만 (순서 유지).

        Args:
            frames: 원하는 시간축들.

        Returns:
            줄 수 있는 것만 남긴 튜플. 조립부가 거래소별 축 차이를 isinstance 로 알지 않게 한다.
        """
        ...

    def candle_stream(
        self, instruments: Sequence[Instrument], timeframe: Timeframe, multiplier: Decimal
    ) -> CandleStream:
        """이 거래소의 라이브 캔들 스트림 — 어느 구체 스트림인지는 어댑터가 안다.

        Args:
            instruments: 구독할 종목들.
            timeframe: 봉 간격.
            multiplier: 계약 승수 — 거래량을 계약 수에서 기초자산 수량으로 옮기는 값.

        Returns:
            아직 열지 않은 스트림. 소비자가 `stream()` 으로 연다.
        """
        ...


@runtime_checkable
class OrderBookAdapter(BrokerAdapter, Protocol):
    """호가창 조회 확장 인터페이스 (spec §12.7 비용 테이블의 측정 경로).

    ## 왜 `BrokerAdapter` 에 8번째 메서드로 넣지 않았는가

    spec §4.2 는 어댑터 메서드를 **7종**으로 명시했고, 호가는 그중에 없다. 필수 메서드로
    올리면 호가를 제공하지 않는 브로커의 어댑터가 전부 "예외를 던지는 구현"을 갖게 되고,
    그 순간 `capabilities` 선언이 무의미해진다 — 능력 조회 없이 부르면 터지는 메서드를
    프로토콜이 있다고 말하게 된다.

    같은 파일의 `DerivativesAdapter` 가 이미 이 패턴이다: **핵심 계약은 건드리지 않고
    확장은 별도 프로토콜 + `Capability` 선언으로 드러낸다.**

    ## 호출부 규약

    ```python
    adapter = provider.adapter_for(Market.UPBIT)  # 절대 규칙 #0
    if Capability.ORDERBOOK not in adapter.capabilities or not isinstance(
        adapter, OrderBookAdapter
    ):
        raise SystemExit("이 브로커는 호가를 주지 않는다")
    book = await adapter.get_orderbook(instrument)
    ```

    Note:
        Phase 1 에서 이것을 쓰는 곳은 **비용 실측**뿐이다 (`scripts/runtime/measure_costs.py`).
        슬리피지는 D1-6 에서 편도 10bp 로 **가정**됐고 그 가정이 필요 승률표의 모든 셀에
        분모로 들어간다 — 15m 판정이 가정에 따라 18%p 갈리므로 측정이 먼저다.

        P2 집행에서는 같은 메서드가 분할 진입의 예상 체결가 산정에 쓰인다
        (`OrderBook.walk`). 측정용 계산을 따로 만들지 않은 이유다.
    """

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """현재 호가창 스냅샷을 반환한다.

        Args:
            instrument: 대상 종목.

        Returns:
            최우선 호가부터의 단계들. `as_of` 는 **요청 직전에 우리가 잰 시각**이다
            (spec §4.18 — 브로커 시계를 신뢰하지 않는다).

        Note:
            단계 수는 브로커가 정한다 (업비트 30단계). 호출부가 특정 개수를 가정하면
            브로커 교체 시 조용히 얕은 호가창으로 계산하게 되므로, 깊이가 모자란 것은
            `OrderBook.walk` 의 `is_complete=False` 로만 드러낸다.
        """
        ...


@runtime_checkable
class DerivativesAdapter(BrokerAdapter, Protocol):
    """파생 상품 확장 인터페이스 (spec §4.2 — **현 Phase 범위 밖, 자리만**).

    spot 어댑터는 구현하지 않으며 기존 인터페이스는 불변이다.

    Note:
        업비트는 현물 전용이라 이 인터페이스를 쓸 수 없고, 해외 파생 거래소는 한국
        거주자 이용 제한·규제 리스크가 있다 (spec §12.8). **어댑터 추상화에 자리만
        유지**한다.

        레버리지의 올바른 역할은 리스크 증폭이 아니라 **증거금 효율**이다. 포지션
        크기는 여전히 리스크 % 규칙으로 산정하며, 공격성은 레버리지가 아니라 프리셋의
        리스크 % 상향으로 구현한다 (spec §4.2, §4.6).

        안전 규칙: **청산가는 손절가보다 항상 바깥 + 버퍼** — 위반 시 레버리지를
        자동 축소한다. 거래소 강제청산이 시스템 손절을 선점하는 상황을 구조적으로
        차단하기 위해서다 (spec §4.2).
    """

    async def set_leverage(self, instrument: Instrument, leverage: Decimal) -> None:
        """레버리지 배수를 설정한다.

        Args:
            instrument: 종목.
            leverage: 배수. 값은 결정(decision)이 정한다.
        """
        ...

    async def get_margin_info(self, instrument: Instrument) -> Balance:
        """증거금 정보를 조회한다.

        Args:
            instrument: 종목.

        Returns:
            증거금을 담은 잔고 객체.
        """
        ...

    async def get_liquidation_price(self, instrument: Instrument) -> Decimal:
        """강제청산가를 조회한다.

        Args:
            instrument: 종목.

        Returns:
            강제청산가. 손절가보다 항상 바깥이어야 한다 — 안쪽이면 손절이 아니라 청산이 먼저 온다.
        """
        ...

    async def get_funding_rate(self, instrument: Instrument) -> Decimal:
        """펀딩비율을 조회한다.

        Args:
            instrument: 종목.

        Returns:
            다음 정산의 펀딩비율. 비용 모델(spec §12.7)에 포함해야 한다.
        """
        ...


class RequestBudgetExceededError(RuntimeError):
    """한 작업의 브로커 요청 수가 예산을 넘었다 (T253).

    `RequestCounting.budget` 블록 안에서 난다. 잡는 쪽은 사람에게 말한다(규칙 #8) —
    조용히 계속 부르면 요율 한도(토큰 하나)를 다른 판까지 잃는다.
    """


@runtime_checkable
class RequestCounting(Protocol):
    """브로커 요청을 세고 예산을 걸 수 있는 어댑터 (T253).

    폴링 브로커(토스)처럼 봉 하나가 요청 수십 개인 경로가 대상이다. 웹소켓 거래소는
    구현하지 않아도 된다 — 조립부가 `isinstance` 로 가려 없으면 재지 않는다.
    """

    @property
    def requests(self) -> int:
        """지금까지 보낸 HTTP 요청 수 (프로세스 누계)."""
        ...

    def budget(self, cap: int) -> AbstractContextManager[None]:
        """블록 안에서 `cap` 개를 넘는 요청은 `RequestBudgetExceededError`.

        Args:
            cap: 허용 요청 수. 0 이하면 무제한.
        """
        ...
