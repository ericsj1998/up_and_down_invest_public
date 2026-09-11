"""시세 조회 어댑터 획득 지점 (P0-9-7 · spec §4.2 / 절대 규칙 #0).

## 왜 이 파일이 있는가

절대 규칙 #0 은 "**주문 경로**는 반드시 `OrderGateway` 를 통해 어댑터를 얻는다"다. 그리고
그 규칙을 정적 검사(`tests/test_gateway_bypass.py`)가 강제하는데, 검사는 구체 어댑터
패키지의 **모든 import** 를 잡는다 — 조회 목적이어도 잡힌다.

캔들 수집(P0-8-6)은 조회 전용이고 주문과 무관하다. 그렇다고 `apps/engine/main.py` 를
검사 허용 목록에 넣으면 **가드의 이빨이 빠진다** — 그 파일에서 나중에 무엇을 import 해도
통과하게 된다.

그래서 **조회 전용 획득 지점을 하나** 둔다. 결과적으로 구체 어댑터를 만질 수 있는 파일은
정확히 둘이다:

| 파일 | 용도 | 반환하는 것 |
|---|---|---|
| `execution/gateway.py` | **주문** 경로 | Phase 0~1 에는 아무것도 (예외를 던진다) |
| `marketdata/provider.py` | **조회** 경로 | 조회 전용 어댑터 |

## 이것은 주문 게이트의 우회로가 아니다

여기서 얻는 어댑터는 `submit_order` 를 부르면 `OrderPathNotAvailableError` 를 던진다
(P0-7-6). 즉 조회 경로로 어댑터를 얻어도 **주문할 수단이 없다.** 그 차단이 어댑터 자체에
있으므로 이 파일이 규칙 #0 을 약화시키지 않는다.

Phase 2 에서 주문이 열릴 때도 이 파일은 바뀌지 않는다 — 주문 가능한 어댑터는 `OrderGateway`
가 별도로 만들며, 그것이 라이브 이중 게이트(§12.4)를 통과한 경로다.
"""

import os
from collections.abc import Mapping
from types import TracebackType
from typing import ClassVar, Self

from updown.common.config import ConfigurationError, Settings, load_settings
from updown.common.domain.fundamentals import FundamentalsConfig
from updown.common.domain.instrument import Market, MarketListing
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import BrokerAdapter, QuoteAdapter
from updown.marketdata.binance.adapter import BinanceAdapter
from updown.marketdata.binance.client import BinanceClient
from updown.marketdata.binance.venue import binance_base, binance_ws
from updown.marketdata.fundamentals.adapter import FundamentalsAdapter
from updown.marketdata.fundamentals.client import EdgarClient
from updown.marketdata.fundamentals.edgar import EdgarAdapter
from updown.marketdata.gate.adapter import GateAdapter
from updown.marketdata.gate.client import GateClient
from updown.marketdata.macro.adapter import MacroAdapter
from updown.marketdata.macro.client import MacroClient
from updown.marketdata.toss.adapter import (
    CHART_GROUP,
    MARKET_DATA_GROUP,
    STOCK_GROUP,
    ResultClient,
    TossAdapter,
)
from updown.marketdata.toss.client import TossApiError as TossApiError
from updown.marketdata.toss.client import TossAuthError as TossAuthError
from updown.marketdata.toss.client import TossClient
from updown.marketdata.toss.client import UnknownSymbolError as UnknownSymbolError
from updown.marketdata.toss.proxy_client import TossProxyClient
from updown.marketdata.upbit.adapter import UpbitAdapter
from updown.marketdata.upbit.client import UpbitClient

_logger = get_logger("marketdata.provider")

#: 토스 어댑터가 담당하는 시장.
_TOSS_MARKETS = frozenset({Market.KRX, Market.NASDAQ, Market.NYSE})

#: 토스 프록시(T275)가 대신 불러 주는 경로 — 어댑터가 쓰는 넷뿐. 그 밖은 서버가 400.
TOSS_PROXY_PATHS = frozenset(
    {"/api/v1/candles", "/api/v1/orderbook", "/api/v1/prices", "/api/v1/stocks"}
)
#: 프록시가 받는 요율 그룹 — 서버 스로틀 키가 되므로 어댑터의 셋만.
TOSS_PROXY_GROUPS = frozenset({CHART_GROUP, MARKET_DATA_GROUP, STOCK_GROUP})


def market_allowlist(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """`UPDOWN_MARKETS` 가 허용한 시장 이름들 — 비면 제한 없음.

    Args:
        env: 환경 (시험용). None 이면 `os.environ`.

    Returns:
        대문자 시장 코드 집합. `NONE` 처럼 시장이 아닌 이름도 그대로 들어간다(아무 시장도
        안 맞는다).
    """
    raw = (os.environ if env is None else env).get("UPDOWN_MARKETS", "")
    return frozenset(m.strip().upper() for m in raw.split(",") if m.strip())


def toss_allowed(allow: frozenset[str]) -> bool:
    """이 프로세스가 토스를 불러도 되는가 — 허용 목록에 토스 시장(KRX·NASDAQ·NYSE)이 있으면.

    Args:
        allow: `market_allowlist()`.

    Returns:
        비면 True(제한 없음). 있으면 토스 시장 포함 여부.

    Note:
        토스는 client 당 access token 이 하나라 **키를 가진 프로세스가 둘이면 서로를 무효화**한다.
        `UPDOWN_MARKETS=BINANCE` 인 로컬 데모가 저평가 화면 준비(종가 일봉)로 토스를 30분에 300번
        불러 서버 시세를 계속 끊었다(2026-09-11 실측). 시장 목록에 토스 시장이 없으면 어댑터를
        **아예 만들지 않는다** — 그러면 토큰도 안 받는다.
    """
    return not allow or any(m.value in allow for m in _TOSS_MARKETS)


class UnsupportedMarketError(ValueError):
    """조회 어댑터가 없는 시장이다.

    Note:
        조용히 다른 시장의 어댑터로 대체하지 않는다 — KRX 종목을 코인 API 에 묻는 것은
        빈 결과가 아니라 **틀린 결과**를 낳는다.
    """


class MarketDataProvider:
    """시세 조회 어댑터를 제공하고 그 수명을 관리한다.

    Note:
        어댑터가 아니라 **provider** 인 이유는 HTTP 클라이언트의 수명 때문이다.
        어댑터만 돌려주면 커넥션 풀을 닫을 주체가 사라져, 조회 경로가 소켓을 누수한다.

        `async with` 로 쓰거나 `aclose()` 를 직접 부른다.
    """

    # ⭐ 코인 조회 클라이언트는 **프로세스 공유**다 (2026-09-05) — `adapter_for` 참조. 인스턴스마다
    #    만들면 요청마다 연결 풀·rate-limit 계량기가 새로 생긴다.
    _shared_gate: ClassVar[GateClient | None] = None
    _shared_binance: ClassVar[BinanceClient | None] = None
    _shared_toss: ClassVar[ResultClient | None] = None

    def __init__(self, settings: Settings | None = None) -> None:
        """Provider 를 만든다. 실제 클라이언트는 시장별로 지연 생성한다.

        Args:
            settings: 설정. 생략하면 토스 자격증명이 **실제로 필요해진 시점**에 읽는다 —
                코인만 쓰는 경로가 주식 자격증명 부재로 죽지 않게 하기 위해서다.
        """
        self._settings = settings
        self._upbit_client: UpbitClient | None = None
        self._toss_client: ResultClient | None = None
        # ⭐ Gate 는 **자격증명이 없다** — 조회 엔드포인트가 전부 공개라서, 키를 안 쓰는
        #   것 자체가 주문 차단 방어선이다 (업비트와 같은 방향 · spec §8).
        self._gate_client: GateClient | None = None
        self._binance_client: BinanceClient | None = None

    def live_markets(self) -> tuple[str, ...]:
        """QuoteAdapter 계약을 지키는 시장들 — 라이브 배선 가능 목록의 SSoT (T63 §2c).

        Returns:
            시장 코드 튜플 (지금은 BINANCE·GATE).

        Note:
            화면 선택창·거래소별 리포트가 전부 여기서 파생된다 — 목록을 손으로 적는
            곳이 없어야 새 거래소가 계약을 지키는 순간 전부 자동으로 늘어난다.
            조회 자격증명이 없는 시장은 조용히 건너뛴다 (그 시장이 없는 것과 같다).

            ⭐ `UPDOWN_MARKETS=GATE` 처럼 환경변수로 **더 좁힐 수 있다** (2026-09-05 · 배포는
            Gate 만). 없으면 계약을 지키는 시장 전부다. 배포 서버에 Binance 키가 없는데 콘솔·
            대조가 Binance 를 계속 물어 경고가 분당 수십 건 쌓였다 — 목록을 여기서 좁히면
            그 위의 화면·리포트·대조가 전부 같이 좁아진다 (한 곳이 SSoT).
        """
        allow = market_allowlist()
        found: list[str] = []
        for market in Market:
            if allow and market.value not in allow:
                continue
            try:
                adapter = self.adapter_for(market)
            except Exception:
                continue
            if isinstance(adapter, QuoteAdapter):
                found.append(market.value)
        return tuple(found)

    def broker_of(self, market: Market) -> str:
        """이 시장의 봉·시세를 대는 브로커 이름 — 화면의 브로커 마크가 읽는다 (T245).

        Args:
            market: 시장.

        Returns:
            `toss` · `upbit` · `gate` · `binance`.
        """
        if market in _TOSS_MARKETS:
            return "toss"
        return market.value.lower()

    def adapter_for(self, market: Market) -> BrokerAdapter:
        """시장에 맞는 조회 어댑터를 준다.

        Args:
            market: 대상 시장.

        Returns:
            조회 전용 어댑터. **주문 메서드는 예외를 던진다**.

        Raises:
            UnsupportedMarketError: 조회 어댑터가 없는 시장.
            ConfigurationError: 주식 시장인데 조회 자격증명이 없는 경우.

        Note:
            같은 시장을 여러 번 요청하면 **같은 클라이언트를 공유**한다. 매번 새로 만들면
            rate limit 스로틀도 새로 생겨 그룹별 예산 관리가 무의미해진다 (P0-7-2).
            주식 3개 시장(KRX·NASDAQ·NYSE)도 **한 클라이언트**를 공유한다 — 토스의 rate
            limit 은 시장이 아니라 **엔드포인트 그룹**별이기 때문이다.
        """
        if market is Market.GATE:
            # 🔴 **선물이다.** 여기서만 숏·레버리지가 열린다 (절대 규칙 #10 개정) —
            #    양방향 플레이북(숏 24%)을 그대로 돌릴 수 있는 유일한 경로다.
            #
            #    ⚠️ 라이브 베이스로 붙는다. testnet 은 주문 경로를 검증하는 곳이고
            #      **조회는 라이브를 봐야 한다** — testnet 호가창은 라이브와 다르므로
            #      거기서 잰 값으로 판단하면 다른 시장을 분석하는 셈이다.
            # ⭐ **프로세스에서 하나만** (2026-09-05 · 1 GB 서버 실측). `MarketDataProvider()` 를
            #    요청마다 새로 만드는 호출부가 22곳이라 인스턴스 캐시로는 10분에 클라이언트 172개가
            #    생겼다 — 각각 새 연결 풀·TLS 핸드셰이크·rate-limit 계량기다. 클래스 수준으로
            #    올린다.
            if MarketDataProvider._shared_gate is None:
                MarketDataProvider._shared_gate = GateClient()
                _logger.info("market_data_adapter_created", payload={"market": market.value})
            self._gate_client = MarketDataProvider._shared_gate
            return GateAdapter(self._gate_client)

        if market is Market.BINANCE:
            # 🔴 **주문이 나가는 곳을 본다** (2026-08-30 사용자 지적: *"테스트넷 콘솔은
            #    테스트넷 쓰는거고, 라이브로 넘어갔을 때 그걸로 쓰는게 맞지"*).
            #
            #    전에는 Gate 를 따라 라이브 베이스로 고정했는데, 바이낸스에서는 그
            #    선택이 두 가지를 한꺼번에 깼다:
            #
            #      ① **라이브 선물 웹소켓이 이 네트워크에서 데이터를 안 준다.**
            #         구독은 성공하고(`result:null`) kline 이 0건이다 — 실측으로
            #         경로구독·합친스트림·aggTrade 전부 0건. 그래서 20초 REST 폴링으로
            #         내려갔고, 차트가 20초에 한 번 움직였다
            #      ② 주문은 testnet 에서 체결되는데 화면은 라이브 호가를 그렸다 —
            #         **다른 시장을 보면서 이 시장에 주문**하는 것이다. 이 프로젝트는
            #         그 어긋남으로 이미 사고를 냈다 (라이브 명세로 만든 손절가가
            #         testnet 에서 거절)
            #
            #    ⇒ 조회 베이스를 주문 환경에 맞춘다. testnet 주문이면 testnet 시세,
            #      라이브 주문이면 라이브 시세. 실측(60초):
            #
            #        testnet 선물 WS  갱신 113건 · 라이브 대비 괴리 중앙 -0.025%
            #        testnet REST     400봉 전부 · 거래 0인 봉 0%
            #
            # ⚠️ Gate 는 그대로 라이브다 — Gate testnet 호가창은 라이브와 다르고,
            #    Gate 라이브 웹소켓은 멀쩡히 돈다. 같은 규칙이 두 거래소에서 다른
            #    답을 내는 것이 맞다 (사실이 다르기 때문이다).
            if MarketDataProvider._shared_binance is None:
                MarketDataProvider._shared_binance = BinanceClient(base_url=binance_base())
                _logger.info(
                    "market_data_adapter_created",
                    payload={"market": market.value, "base": binance_base()},
                )
            self._binance_client = MarketDataProvider._shared_binance
            return BinanceAdapter(self._binance_client, ws_url=binance_ws())

        if market is Market.UPBIT:
            if self._upbit_client is None:
                self._upbit_client = UpbitClient()
                _logger.info("market_data_adapter_created", payload={"market": market.value})
            return UpbitAdapter(self._upbit_client)

        if market in _TOSS_MARKETS:
            return TossAdapter(self.toss_client(market))

        raise UnsupportedMarketError(f"{market} 의 조회 어댑터가 없다")

    def toss_via_proxy(self) -> bool:
        """이 프로세스가 토스를 **프록시로** 부르는 구성인가 (`TOSS_PROXY_URL/TOKEN` · T275).

        Returns:
            둘 다 설정돼 있으면 True. 반쪽이면 `ConfigurationError`.
        """
        settings = self._settings or load_settings()
        return settings.toss_proxy is not None

    def toss_client(self, market: Market = Market.NASDAQ) -> ResultClient:
        """프로세스 공유 토스 조회 client — 직접(`TossClient`) 또는 프록시(`TossProxyClient`).

        Args:
            market: 로그·오류 문구용 시장. 어느 토스 시장이든 client 는 하나다.

        Returns:
            `ResultClient`. 처음 부를 때 만든다.

        Raises:
            UnsupportedMarketError: `UPDOWN_MARKETS` 에 토스 시장이 없다 — 어댑터도
                토큰도 안 만든다.
            ConfigurationError: 자격증명/프록시 설정이 없거나 반쪽이다 · 실계좌 서버가
                프록시 client 로 구성됐다.

        Note:
            🔴 **프로세스에서 하나** (T250 실측 2026-09-09). 토스는 client 당 토큰이
            하나라 두 판이 각자 클라이언트를 들면 서로의 토큰을 무효화한다
            (`token-revoked` 401 → 재발급 반복). Gate 와 같은 이유로 클래스 수준에 둔다.
            프로세스 사이(서버 ↔ 연구 PC)는 **프록시**(T275)로 발급 주체를 서버
            하나에 모은다 — 서버 `/admin/toss/result`(`apps/api/toss_proxy.py`)가
            이 메서드의 직접 client 로 대신 부른다.
        """
        if not toss_allowed(market_allowlist()):
            raise UnsupportedMarketError(
                f"{market.value}: 이 프로세스는 토스를 부르지 않는다 — "
                "UPDOWN_MARKETS 에 KRX/NASDAQ/NYSE 가 없다 (토큰은 client 당 하나)"
            )
        if MarketDataProvider._shared_toss is None:
            settings = self._settings or load_settings()
            proxy = settings.toss_proxy
            if proxy is not None:
                if settings.is_live:
                    raise ConfigurationError(
                        "실계좌 서버는 토스 프록시 client 가 될 수 없다 — 발급 주체는 서버 하나다. "
                        ".env.live 에서 TOSS_PROXY_URL/TOKEN 을 지운다"
                    )
                url, token = proxy
                MarketDataProvider._shared_toss = TossProxyClient(url, token)
                _logger.info(
                    "market_data_adapter_created",
                    payload={"market": market.value, "broker": "toss", "via": "proxy"},
                )
            else:
                client_id, client_secret = settings.toss_market_data_credentials
                MarketDataProvider._shared_toss = TossClient(client_id, client_secret)
                _logger.info(
                    "market_data_adapter_created",
                    payload={"market": market.value, "broker": "toss"},
                )
        self._toss_client = MarketDataProvider._shared_toss
        return self._toss_client

    async def list_symbols(self, market: Market) -> list[str]:
        """그 시장에서 조회 가능한 종목 코드들 (Phase 5 §5-6 종목 검색).

        Args:
            market: 대상 시장.

        Returns:
            종목 코드 목록.

        Raises:
            UnsupportedMarketError: 종목 목록을 낼 수 없는 시장. **빈 목록을 돌려주지
                않는다** — 검색창이 "결과 없음"을 보여 주면 사용자는 종목이 없다고
                믿지만 실제로는 기능이 없는 것이다 (절대 규칙 #8).

        Note:
            `BrokerAdapter` 프로토콜에 `list_markets` 를 올리지 않은 이유는 토스에
            같은 개념이 없기 때문이다 — 없는 메서드를 프로토콜에 넣으면 구현체가
            `NotImplementedError` 로 채워지고, 그러면 프로토콜이 계약이 아니게 된다.
            대신 **구체 어댑터를 아는 유일한 자리인 여기서** 시장별로 갈린다 (규칙 #0).
        """
        if market is Market.UPBIT:
            adapter = self.adapter_for(market)
            if not isinstance(adapter, UpbitAdapter):  # pragma: no cover - 방어
                raise UnsupportedMarketError("업비트 어댑터를 얻지 못했다")
            return await adapter.list_markets()
        raise UnsupportedMarketError(
            f"{market.value} 는 종목 목록 조회를 아직 지원하지 않는다 — "
            "토스는 종목 마스터 API 가 따로 있고 P2-9 소관이다"
        )

    async def list_listings(self, market: Market, quote: str = "") -> list[MarketListing]:
        """종목을 **이름·현재가·거래대금과 함께** 준다 (Phase 5 §5-6 종목 선택).

        Args:
            market: 대상 시장.
            quote: 기준통화 접두 (`KRW`). 비면 전부.

        Returns:
            거래대금 내림차순 목록.

        Raises:
            UnsupportedMarketError: 아직 지원하지 않는 시장.

        Note:
            `list_symbols` 를 두고 이것을 따로 두는 이유는 **비용**이다. 코드만 필요한
            호출부(WS 구독 전 검증·시드)까지 `/ticker` 를 281종목분 부르게 할 이유가 없다.
            화면처럼 사람이 고를 목록이 필요할 때만 이쪽을 쓴다.
        """
        if market is Market.UPBIT:
            adapter = self.adapter_for(market)
            if not isinstance(adapter, UpbitAdapter):  # pragma: no cover - 방어
                raise UnsupportedMarketError("업비트 어댑터를 얻지 못했다")
            return await adapter.list_listings(quote)
        raise UnsupportedMarketError(
            f"{market.value} 는 종목 목록 조회를 아직 지원하지 않는다 — "
            "토스는 종목 마스터 API 가 따로 있고 P2-9 소관이다"
        )

    async def __aenter__(self) -> Self:
        """컨텍스트 진입."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """컨텍스트 이탈 — 연결을 닫는다."""
        await self.aclose()

    async def aclose(self) -> None:
        """열린 클라이언트를 **전부** 닫는다.

        Raises:
            BaseExceptionGroup: 하나라도 닫기에 실패하면 전부 닫은 뒤 모아서 던진다.

        Note:
            한쪽 종료가 실패해도 나머지를 닫는다 — 먼저 실패한 예외에 가려 다른 커넥션
            풀이 영원히 열려 있는 상황을 막는다.
        """
        # ⚠️ 새 클라이언트를 더할 때 **여기도** 채운다. 빠뜨리면 소켓이 누수되는데
        #    증상이 조용하다 — 프로세스가 오래 살아야 드러난다.
        # ⛔ 코인 조회 클라이언트(Gate·Binance)와 **토스**(T250 · 2026-09-09)는 프로세스 공유라
        #    여기서 닫지 않는다 (2026-09-05).
        #    `async with MarketDataProvider()` 한 번이 라이브 러너의 클라이언트까지 닫아
        #    "client has been closed" 174건 — 공유 자원의 수명은 프로세스다. 토스도 공유로
        #    올린 날 같은 증상이 재현됐다(rank60 의 async with 가 닫음 → 주식 판 전부 실패).
        pending: list[UpbitClient] = [
            client for client in (self._upbit_client,) if client is not None
        ]
        self._upbit_client = None
        self._toss_client = None
        self._gate_client = None
        self._binance_client = None

        failures: list[BaseException] = []
        for client in pending:
            try:
                await client.aclose()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup("조회 클라이언트 종료 실패", failures)


_macro_shared: MacroAdapter | None = None


def macro_adapter() -> MacroAdapter:
    """거시 지표 어댑터 — **조회 경로의 유일한 획득 지점** (절대 규칙 #0 · T262). 프로세스에 하나.

    Returns:
        공개 출처(야후·뉴욕연준·BLS·CBOE) + 토스(환율·국내 지표). 토스 자격증명이 없으면 그 지표들만
        실패 목록으로 간다 — 나머지는 산다.
    """
    global _macro_shared
    if _macro_shared is None:
        toss: TossAdapter | None = None
        try:
            found = MarketDataProvider().adapter_for(Market.NASDAQ)
            toss = found if isinstance(found, TossAdapter) else None
        except Exception as exc:
            _logger.info("macro_toss_unavailable", payload={"detail": str(exc)[:120]})
        _macro_shared = MacroAdapter(MacroClient(), toss)
    return _macro_shared


def fundamentals_adapter(settings: Settings, config: FundamentalsConfig) -> FundamentalsAdapter:
    """재무 출처 어댑터 — **조회 경로의 유일한 획득 지점** (절대 규칙 #0 · T243).

    Args:
        settings: `edgar_user_agent` 를 읽는다.
        config: 개념 매핑.

    Returns:
        EDGAR 어댑터. 국내(DART)는 아직 없다.

    Raises:
        ConfigurationError: `EDGAR_USER_AGENT` 가 비었다 — SEC 는 이름·이메일 없는 요청을
            403 으로 막으므로 조용히 만들지 않는다 (규칙 #8).
    """
    if not settings.edgar_user_agent:
        raise ConfigurationError(
            "EDGAR_USER_AGENT 가 비었다 — SEC 는 이름·이메일 없는 요청을 403 으로 막는다"
        )
    return EdgarAdapter(EdgarClient(settings.edgar_user_agent), config)
