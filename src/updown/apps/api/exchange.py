"""거래소 콘솔 — **거래소가 실제로 들고 있는 것**을 보고 앱에서 정리한다.

## 🔴 왜 필요한가

사용자 요구 2026-08-18:

> *"거래소 주문 보기 탭을 하나 만들어서, 거기서 삭제할 수 있게 해줘. 실제 라이브라면
> 거래소에서 닫아도 되겠지만, 나는 내 앱에서도 다 컨트롤 하는 게 목표야."*

앞서 실제로 그 상황이 났다. RUN 을 지우면 러너만 멈추고 **포지션은 거래소에 남았고**,
목록에서 사라진 뒤에는 앱에서 손댈 방법이 없었다 — 거래소 웹에 로그인해야 했다.

## ⚠️ 여기는 원장이 아니다

    원장(`/walkforward/*`)   손익률을 곱해 나가는 **모형**
    이 화면                  거래소가 말하는 **사실**

둘이 갈리는 것이 이 프로젝트에서 가장 위험한 상태였다(유령 포지션). 그래서 이 화면은
원장을 **한 번도 읽지 않는다** — 섞으면 무엇을 보고 있는지 잃는다.

## ⛔ 실주문 경로가 아니다

어댑터는 `OrderGateway` 에서만 받는다 (절대 규칙 #0). 게이트의 LIVE 분기는 여전히
예외이고 `GatePaperAdapter` 는 testnet 아닌 클라이언트를 거부한다.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException, Request

from updown.apps.api.market_hours import market_status_payload
from updown.common.cache import TtlCache
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.capabilities import CapabilityConfigError, capabilities_of
from updown.common.domain.instrument import Instrument, Market, MarketGroup, Timeframe
from updown.common.domain.order import OrderKind, OrderRequest, OrderType, Side
from updown.common.domain.session import SessionConfigError, load_calendar
from updown.common.logging.setup import get_logger
from updown.common.security.roles import Role
from updown.execution.gateway import OrderGatewayError, order_adapter
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.calendar_check import (
    COUNTRY_OF,
    CalendarSource,
    compare_day,
    regular_window_of,
)
from updown.marketdata.gate.adapter import GateAdapter
from updown.marketdata.provider import MarketDataProvider
from updown.marketdata.ratelimit import meter
from updown.orchestration.liquidity import Liquidity, escape_plan, probe_book
from updown.orchestration.report.performance import Window, summarize_account_book
from updown.orchestration.walkforward.live_runner import (
    MARGIN_HEADROOM,
    PositionLister,
    trade_of_text,
)

router = APIRouter(prefix="/exchange", tags=["exchange"])

# ⭐ 콘솔 조회 TTL 캐시 (2026-08-25 사고 — Docker VM 시계 +1.5일 점프로 타이머가 폭주해
#   BN testnet 이 IP 밴(418 -1003)을 냈다). 시계와 별개로, 바이낸스 서명 가중치
#   (income 30 · account 5)는 4초 화면 폴링을 감당 못 한다 — 화면이 몇 탭이든
#   거래소로는 TTL 에 한 번만 나간다. 값은 관찰용 화면 기준이라 신선도 손해가 없다.
#   ⛔ 주문·취소·청산(집행)은 캐시하지 않는다 — 상태 조회만이다.
_STATE_CACHE = TtlCache[dict[str, Any]]("exchange.state", 5.0)
_CACHE_TTL_S = {"GATE": 10.0, "BINANCE": 20.0, "BALANCES": 30.0}
"""콘솔 상태 캐시 TTL — GATE 3 → 10s (T268 #7 · 2026-09-11).

4초 폴링 x 종목 9 가 요율 156% 를 만들던 자리다."""


async def _cached(key: str, market: str, build: Any) -> dict[str, Any]:
    """TTL 안이면 저장분을, 지나면 한 번만 다시 만든다 (동시 요청은 키 락으로 합류)."""
    return await _STATE_CACHE.get_or_fetch(key, build, ttl_s=_CACHE_TTL_S.get(market, 5.0))


@router.get("/markets")
async def markets() -> dict[str, Any]:
    """이 API 가 붙어 있는 거래소 목록 — 콘솔이 무엇을 물을지 여기서 정한다 (2026-09-06).

    Returns:
        `{markets: ["GATE", ...]}`. **빈 목록이면 거래소 연결이 없는 API 다** — 로컬 실계좌 모드
        (화면·원장·리포트만)가 그렇다. 화면은 GATE 를 기본으로 박아 두지 않고 이 목록을 따른다.

    Note:
        출처는 `MarketDataProvider.live_markets()` 하나다 (`UPDOWN_MARKETS` 로 좁힌다 ·
        `NONE` 이면 빈 목록).
    """
    provider = MarketDataProvider()
    scoped = set(provider.live_markets())
    known: list[dict[str, Any]] = []
    for market in Market:
        try:
            quotes = provider.adapter_for(market)
        except Exception:
            continue
        if not isinstance(quotes, QuoteAdapter):
            continue
        try:
            order_adapter(quotes, user_id="console")
            ready = True
        except Exception:
            ready = False
        # ⭐ T245 — 시장 묶음(코인/주식)과 브로커는 **서버가 말한다**. 화면은 이름으로 안 가른다.
        # ⭐ 능력표(T238)도 같이 — 배율·청산·펀딩 칸을 숨길지는 화면이 이 값으로 정한다.
        try:
            caps = capabilities_of(market)
            traits: dict[str, Any] = {
                "leverage": caps.leverage_allowed,
                "short": caps.short_allowed,
                "funding": caps.funding,
                "always_open": caps.always_open,
            }
        except CapabilityConfigError:
            traits = {}
        known.append(
            {
                "name": market.value,
                "ready": ready,
                "scoped": market.value in scoped,
                "group": "coin" if MarketGroup.of(market) is MarketGroup.COIN else "stock",
                "broker": provider.broker_of(market),
                # ⭐ 재무 출처가 있는 시장인가 (T243 = EDGAR = 해외주식). 저평가 카드는 이 값이 참인
                #    시장만 부른다 — KRX 를 고르면 400 이 뜨던 것(사용자 신고 2026-09-10).
                "fundamentals": MarketGroup.of(market) is MarketGroup.FOREIGN_STOCK,
                **traits,
            }
        )
    # `markets` = 이 API 가 실제로 붙어 있는 거래소(범위 안 · 키 있음) — 폴링·대조가 도는 곳.
    # `all` = 아는 거래소 전부 + 연결 여부 — 콘솔 칩은 이걸 그려 사람이 고른다 (사용자 2026-09-06:
    # "기능은 있되, 선택권을 사람이 가지는 거지"). 연결 안 된 것을 고르면 "API 키 설정이
    # 필요합니다".
    return {"markets": [m["name"] for m in known if m["ready"] and m["scoped"]], "all": known}


@router.get("/market-status")
async def market_status(market: str) -> dict[str, Any]:
    """장 시간 배지 (T245) — 캘린더만 읽는다. 브로커 호출 없음.

    Args:
        market: 시장 코드.

    Returns:
        `market_status_payload` 의 모양.

    Raises:
        HTTPException: 모르는 시장(400) · 캘린더를 못 읽음(503).
    """
    try:
        target = Market(market)
    except ValueError as exc:
        raise HTTPException(400, f"모르는 시장이다: {market}") from exc
    try:
        calendar = load_calendar()
    except (SessionConfigError, OSError) as exc:
        raise HTTPException(503, f"마켓 캘린더를 읽을 수 없다 — {exc}") from exc
    return market_status_payload(calendar, target, datetime.now(UTC))


CALENDAR_CHECK_TTL_S = 3600.0
_CALENDAR_CHECK = TtlCache[dict[str, Any]]("exchange.calendar_check", CALENDAR_CHECK_TTL_S)


@router.get("/calendar-check")
async def calendar_check(market: str) -> dict[str, Any]:
    """토스 장 운영 달력과 우리 캘린더 대조 (T259 2차) — 전일·당일·익일 영업일.

    Args:
        market: 시장 코드 (NASDAQ · NYSE · KRX).

    Returns:
        `{market, country, at, days: [{date, regular}], findings: [...], ok}` — 한 시간 기억.

    Raises:
        HTTPException: 모르는/대조 못 하는 시장(400) · 캘린더 없음·토스 자격증명 없음(503) ·
            토스 실패(502).
    """
    try:
        target = Market(market)
    except ValueError as exc:
        raise HTTPException(400, f"모르는 시장이다: {market}") from exc
    country = COUNTRY_OF.get(target)
    if country is None:
        raise HTTPException(400, f"{target.value} 는 토스 달력이 없다(24시간 장)")
    cached = _CALENDAR_CHECK.get(target.value)
    if cached is not None:
        return cached
    try:
        calendar = load_calendar()
    except (SessionConfigError, OSError) as exc:
        raise HTTPException(503, f"마켓 캘린더를 읽을 수 없다 — {exc}") from exc
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(target)
        if not isinstance(adapter, CalendarSource):
            raise HTTPException(503, "토스 조회 자격증명이 없어 달력을 못 받는다")
        try:
            body = await adapter.market_calendar(country)
        except Exception as exc:
            raise HTTPException(502, f"토스 달력 실패: {str(exc)[:160]}") from exc
    days: list[dict[str, Any]] = [
        cast("dict[str, Any]", body[key])
        for key in ("previousBusinessDay", "today", "nextBusinessDay")
        if isinstance(body.get(key), dict)
    ]
    findings = [f.as_json() for day in days for f in compare_day(calendar, target, day)]
    made: dict[str, Any] = {
        "market": target.value,
        "country": country,
        "at": datetime.now(UTC).isoformat(),
        "days": [{"date": str(d.get("date")), "regular": regular_window_of(d)} for d in days],
        "findings": findings,
        "ok": not findings,
    }
    return _CALENDAR_CHECK.put(target.value, made)


@router.get("/balances")
async def balances() -> dict[str, Any]:
    """`_balances_fresh` 의 TTL 캐시 겉면 (30초) — docstring 은 그쪽에 있다.

    Returns:
        거래소별 잔고. 30초 안의 재요청은 같은 값 — 판 여럿이 같은 계좌를 각자 묻지 않게.
    """
    return await _cached("balances", "BALANCES", _balances_fresh)


async def _balances_fresh() -> dict[str, Any]:
    """거래소별 잔액 — 콘솔 상단 카드가 그린다 (사용자 요구 2026-08-26).

    Returns:
        `{balances: {GATE: {available, position_margin}, BINANCE: {...}}}`.
        못 읽은 거래소는 빠진다 — 화면은 "—" 로 그린다.

    Note:
        어느 거래소를 물을지는 QuoteAdapter 계약에서 파생한다 (T63 §2c) —
        거래소가 늘면 카드도 자동으로 는다. 이 콘솔의 나머지가 Gate 전용이라
        바이낸스 돈이 지갑 카드에만 보이던 것을 상단에서도 보이게 한다.
    """
    provider = MarketDataProvider()
    found: dict[str, Any] = {}
    # ⭐ 오늘 실현 손익의 앵커 = **KST 자정** (사용자 확정 2026-08-26). KST 는 DST 가
    #   없어 고정 +9 가 사실이다 — 저장·질의는 UTC 그대로다 (규칙 #7: 표시 기준만 KST).
    kst_midnight = datetime.now(timezone(timedelta(hours=9))).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today = Window(since=kst_midnight.astimezone(UTC), until=datetime.now(UTC))
    # ⭐ 거래소 목록은 provider 가 정한다 (`UPDOWN_MARKETS` 로 좁힐 수 있다 · 2026-09-05).
    #    전 Market 을 돌면 키 없는 거래소마다 경고가 쌓인다.
    live_names = set(provider.live_markets())
    for market in Market:
        if market.value not in live_names:
            continue
        # 🔴 **밴 중에는 두드리지 않는다** (2026-09-03 실측). 콘솔 폴링이 밴 중에도
        #    account 를 계속 불러 밴이 계속 연장됐다 — ratelimit 모듈 문서의
        #    "2분→74분" 사고의 재판이다. 만료까지 그 거래소 칸만 비운다(화면 "—").
        #    조회 스킵은 리스크 감소·표시 경로라 규칙 8-1 과 충돌하지 않는다.
        if meter(market.value).banned:
            continue
        try:
            quotes = provider.adapter_for(market)
        except Exception:
            continue
        if not isinstance(quotes, QuoteAdapter):
            continue
        try:
            orders = order_adapter(quotes, user_id="console")
            balance = await orders.get_balance()
        except Exception as exc:
            _logger.warning(
                "console_balance_unreadable",
                payload={"market": market.value, "error": str(exc)[:120]},
            )
            continue
        row = {
            "available": str(balance.cash),
            "position_margin": str(balance.positions_value),
            "broker": str(balance.broker),
        }
        # 오늘(KST 00시~) 실현 순손익 — 수수료·펀딩 포함, 미실현 제외. 못 읽으면
        # 칸을 비운다 (0 으로 꾸미지 않는다 · 규칙 #8).
        try:
            book = cast("list[dict[str, Any]]", await cast("Any", orders).account_book(limit=1000))
            rows = [{k: str(v) for k, v in item.items()} for item in book]
            row["today_pnl"] = str(summarize_account_book(rows, today).net)
        except Exception as exc:
            _logger.warning(
                "console_today_pnl_unreadable",
                payload={"market": market.value, "error": str(exc)[:120]},
            )
        # 계정 정체(마진 모드·testnet 여부)까지 주면 상단 카드가 거래소를 바꿔 그린다.
        ident = getattr(orders, "identity", None)
        if ident is not None:
            try:
                info = cast("dict[str, str]", await ident())
                row["margin_mode"] = str(info.get("margin_mode", ""))
                row["testnet"] = str(info.get("testnet", ""))
            except Exception as exc:
                _logger.info(
                    "exchange_identity_failed",
                    payload={"market": market.value, "error": str(exc)[:100]},
                )
        found[market.value] = row
    return {"balances": found}


_logger = get_logger("api.exchange")

DEFAULT_SYMBOL = "BTC_USDT"
"""기본 종목. 화면이 안 보내면 이것을 본다."""

_TRADABLE = frozenset(load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE).spec_ticks)
"""판을 띄울 수 있는 종목 — **호가 눈금이 선언된 것들** (T18 ①·④).

🔴 사용자 확정: *"판을 못 띄우는 것도 맞는 말이야 (…) 그런 종목이 있는 것을 받아들인다"*.
받아들이되 **조용히 넘어가지 않는다** — 순위 창이 그 사실을 열로 말한다.

⛔ 선언 없이 띄우면 옛 원화 기본값(500)으로 떨어지고, 그 경로가 2026-08-18 의 버그였다.

⚠️ **이것은 설정 질문이지 시장 질문이 아니다** (2026-08-20). *"우리가 이 종목의 눈금을
아는가"* 를 물을 뿐, 그 시장이 살아 있는지는 한 글자도 안 본다 — 그래서 `liquidity_of`
가 따로 있고, 돈이 나가는 문(`_live_start`)은 그쪽을 본다.
"""


def _orders_adapter(market: str = "GATE") -> Any:
    """주문 가능한 어댑터를 **게이트를 통해** 얻는다.

    Args:
        market: 거래소 (2026-08-26 — Gate 하드코딩이던 것을 파라미터로. 콘솔이
            거래소별로 같은 기능을 그린다).

    Returns:
        페이퍼 어댑터.

    Raises:
        HTTPException: 자격증명이 없거나 게이트가 막으면 503.

    Note:
        🔴 **직접 만들지 않는다** (절대 규칙 #0 · `test_gateway_bypass.py` 가 정적으로
        강제한다). 구체 어댑터를 아는 파일이 늘면 게이트가 관문이 아니게 된다.
    """
    provider = MarketDataProvider()
    try:
        quotes = provider.adapter_for(Market(market))
    except ValueError as exc:
        raise HTTPException(400, f"모르는 거래소다: {market}") from exc
    try:
        return order_adapter(quotes, user_id="console")
    except OrderGatewayError as exc:
        raise HTTPException(503, f"주문 어댑터를 얻을 수 없다: {exc}") from exc


async def liquidity_of(symbol: str, notional: Decimal, market: str = "GATE") -> Liquidity:
    """**들어가기 전에 나올 수 있는지** 본다 — 어댑터를 얻어 `probe_book` 에 넘긴다.

    Args:
        symbol: 종목.
        notional: 이 판이 굴릴 명목 금액(USDT). 0 이면 깊이는 판정하지 않는다.
        market: 그 판이 주문을 내는 거래소. 🔴 2026-09-06 까지 인자가 없어 **BINANCE 판도 Gate
            호가창**으로 검사했다 — 다른 시장을 보며 이 시장을 판정한 것이고, 로컬 api 에서 Gate
            키를 빼자(S7) 바이낸스 판 여섯이 "GATE 키가 없다" 로 되살아나지 못해 드러났다.

    Returns:
        판정. `ok` 가 거짓이면 `why` 에 이유가 있다.

    Note:
        🔴 **계산은 `orchestration/liquidity.py` 에 있다** (2026-08-20). 러너가 들고 있는
        동안에도 같은 검사를 돌려야 하는데, 러너는 `apps/` 를 import 할 수 없다 —
        두 벌로 두면 화면과 판이 다른 말을 하게 된다.

        🔴 **주문이 나가는 곳(testnet)을 본다.** 조회용 라이브 API 를 보면 증거금 420 을
        15시간 묶은 SPCX 를 *"들어가도 좋다"* 고 답한다 (라이브 격차 0.01% vs testnet 21.8%).
    """
    return await probe_book(_orders_adapter(market), _instrument(symbol, market), notional)


@dataclass(frozen=True, slots=True)
class Sizing:
    """이 예산으로 **이 종목의 최소 주문을 낼 수 있나** (2026-08-20 사고).

    Attributes:
        symbol: 종목.
        mark: 표시가.
        lot: 최소 주문 하나가 몇 코인인가 (`승수 x 최소 계약 수`).
        unit: 그 최소 주문의 명목가(USDT).
        floor: 한 건이라도 나가려면 필요한 **예산 바닥**.
        comfy: 반익까지 되려면 필요한 예산 (다리마다 2계약).
        why: 못 쓰는 이유. 쓸 수 있으면 빈 문자열.

    Note:
        🔴 **종목마다 바닥이 다르다.** Gate 는 계약 크기가 종목마다 100배씩 차이 난다 —
        같은 예산 10 USDT · 3배가 BTC 에서는 다리당 2계약이고 SOL 에서는 0계약이다.
    """

    symbol: str
    mark: Decimal
    lot: Decimal
    unit: Decimal
    floor: Decimal
    comfy: Decimal
    why: str

    @property
    def ok(self) -> bool:
        """한 건이라도 나갈 수 있나."""
        return not self.why


async def sizing_of(
    symbol: str, budget: Decimal, leverage: Decimal, *, leg: Decimal, market: str = "GATE"
) -> Sizing:
    """**예산이 1계약을 살 수 있는지** 본다 (2026-08-20 사용자 신고).

    Args:
        symbol: 종목.
        budget: 이 판의 증거금 예산(USDT).
        leverage: 배율.
        leg: 가장 얇은 진입 다리의 비중 — 사다리면 예산의 절반만 그 다리에 간다.
        market: 그 판이 주문을 내는 거래소 — 계약 명세(승수·최소 수량·표시가)는 거래소마다 다르다
            (`liquidity_of` 와 같은 사연 · 2026-09-06).

    Returns:
        판정. `ok` 가 거짓이면 `why` 에 이유와 필요한 예산이 있다.

    Note:
        🔴 **판이 7시간 돌면서 한 건도 못 냈다.** 사용자 신고: *"이거는 왜 주문이 한번도
        없었어? 말이 안되는데."* 원인은 전략이 아니라 산수였다 (실측 2026-08-20):

        ```
        SOL  15:30  진입0/1 rejected  계약 수 0 가 최소 1 에 못 미친다
                    (자본 4.95 x 3.00배 / 가격 85.375 / 승수 1)
        ETH  17:30  같은 거절
        ```

        예산 10 → 여유 0.99 → 9.9 → 다리 둘로 갈라 **한 다리 4.95**. 3배를 곱해도
        14.85 USDT 인데 SOL 1계약이 87.11 이다. **자리가 아무리 좋아도 못 산다.**

        ⚠️ **화면에서는 "자리가 없었다" 와 구별되지 않았다.** 그래서 문을 여기 세운다 —
        띄우는 순간에 막지 않으면 몇 시간 뒤에 0건을 보고 전략을 의심하게 된다.

        ⛔ **반익 여부로는 막지 않는다.** 1계약은 *"주문이 물리적으로 불가능하다"* 는
        사실이고, 2계약(반익이 되려면 나눠야 한다)은 전략 판단이다 — 사실만 막고 판단은
        문구로 알린다 (스펙에 없는 결정을 만들지 않는다).

        ⛔ **못 읽으면 막지 않는다** — 조회 실패로 판을 못 띄우는 것도 사고다 (§1.2.1).
    """
    empty = Decimal(0)
    orders = _orders_adapter(market)
    instrument = _instrument(symbol, market)
    try:
        spec = await orders.contract_spec(instrument)
    except Exception as exc:
        _logger.warning(
            "sizing_unreadable",
            payload={"symbol": symbol, "error": str(exc)[:140], "note": "막지 않는다"},
        )
        return Sizing(symbol, empty, empty, empty, empty, empty, "")

    mark = Decimal(str(spec.get("mark_price") or spec.get("last_price") or "0"))
    lot = Decimal(str(spec.get("quanto_multiplier", "1"))) * Decimal(
        str(spec.get("order_size_min", 1))
    )
    if mark <= 0 or lot <= 0 or leverage <= 0 or leg <= 0:
        return Sizing(symbol, mark, lot, empty, empty, empty, "")

    unit = mark * lot
    # 🔴 **러너와 같은 계산을 쓴다.** 여유(`MARGIN_HEADROOM`)를 빼먹으면 이 문이
    #    통과시킨 예산으로 러너가 거절당한다 — 문이 거짓말을 하는 것이다.
    floor = unit / leverage / MARGIN_HEADROOM / leg
    why = ""
    if budget < floor:
        why = (
            f"예산 {budget:.2f} x {leverage}배로는 1계약({unit:.2f} USDT)을 못 산다 — "
            f"다리 하나에 예산의 {leg} 만 가므로 최소 {floor:.2f} 이 필요하고, "
            f"반익까지 되려면 {floor * 2:.2f}"
        )
    return Sizing(symbol, mark, lot, unit, floor, floor * 2, why)


def _instrument(symbol: str, market: str = "GATE") -> Any:
    """심볼로 종목을 만든다 — 기본 Gate (2026-08-26 market 파라미터화)."""
    from updown.apps.api.admin import instrument_of

    return instrument_of(symbol, Market(market))


async def _tracked(orders: Any, market: str) -> tuple[str, ...]:
    """이 거래소에서 콘솔이 훑을 종목들 — **파생**이다 (T63 ② · 2026-08-26).

    살아 있는 판의 종목 + 거래소에 실제로 열린 포지션의 종목 + 기본 종목(GATE).
    옛 TRACKED 하드 목록은 testnet 에 없는 종목(SKHY·SNDK)으로 CONTRACT_NOT_FOUND
    경고를 상시 뿜었다 — 그 목록은 순위 창 전용으로 남는다.

    Note:
        거래소 포지션을 합치는 이유: 판이 지워져도 포지션은 남을 수 있고, 그것을
        닫는 것이 이 콘솔의 존재 이유 중 하나다 (모듈 docstring).
    """
    from updown.apps.api.walkforward import LIVE_RUNNERS

    found = {
        runner.instrument.symbol
        for runner in LIVE_RUNNERS.values()
        if runner.instrument.market.value == market
    }
    if market == "GATE":
        found.add(DEFAULT_SYMBOL)
    # 🔴 **어댑터에게 묻는다** (2026-08-30 수정). 전에는 `orders.get_positions()` 를
    #    불렀는데 그건 어댑터가 아니라 **그 안의 TradeClient** 메서드였다 —
    #    `AttributeError` 가 아래 except 에 걸려 warning 으로 삼켜졌고, 그래서
    #    *"거래소에 열려 있는 포지션"* 축이 **몇 달째 조용히 죽어 있었다.**
    #    고아 포지션을 찾는 것이 이 목록의 존재 이유라 그 축이 없으면 반쪽이다.
    #
    # ⚠️ 못 하는 어댑터(업비트 등)는 경고 없이 넘어간다 — 포지션이 없는 시장에서
    #    "포지션을 못 읽는다" 는 경고는 소음이고, 소음은 진짜 경고를 가린다.
    if isinstance(orders, PositionLister):
        try:
            for row in await orders.open_positions():
                if name := row.get("symbol", ""):
                    # 🔴 §4 — 바이낸스 포지션은 `BTCUSDT` 표기다. 내부·`_instrument` 는
                    #    `BASE_QUOTE` 를 요구하므로 이 경계에서 `_` 를 되꽂는다 (안 그러면
                    #    콘솔 이력이 `심볼 규약 밖` 으로 상시 실패한다 · 2026-09-01).
                    if market == "BINANCE":
                        from updown.marketdata.binance.mapping import from_binance

                        name = from_binance(name)
                    found.add(name)
        except Exception as exc:
            _logger.warning(
                "console_tracked_positions_unreadable",
                payload={"market": market, "error": str(exc)[:120]},
            )
    return tuple(sorted(found))


@router.get("/state")
async def state(
    request: Request, symbol: str = DEFAULT_SYMBOL, market: str = "GATE"
) -> dict[str, Any]:
    """`_state_fresh` 의 TTL 캐시 겉면 — docstring 은 그쪽에 있다.

    Args:
        request: 호출자를 읽으려고 받는다 (`request.state.caller`).
        symbol: 종목.
        market: 거래소.

    Returns:
        그 종목의 포지션·주문·조건부 상태. **키 IP 화이트리스트는 관리자에게만** 담긴다.

    Note:
        🔴 화이트리스트는 **서버 주소**다 (사용자 2026-09-12 "관리자 권한이 없으면 보여주지
        말라"). 화면에서만 숨기면 URL 을 아는 사람은 그대로 받는다 — 여기서 지운다.
        응답이 TTL 캐시라 캐시 **뒤에서** 사람마다 지운다. 캐시 안에서 지우면 먼저 부른
        사람의 등급이 뒤에 오는 사람에게 그대로 적용된다.
    """
    return hide_whitelist(
        await console_state(symbol, market), getattr(request.state, "caller", None)
    )


async def console_state(symbol: str = DEFAULT_SYMBOL, market: str = "GATE") -> dict[str, Any]:
    """캐시된 거래소 상태 — **서버 안에서 부르는 문** (고아 쓸기 · 펀드 정리 등).

    Args:
        symbol: 종목.
        market: 거래소.

    Returns:
        `_state_fresh` 의 결과(캐시). 화이트리스트가 **그대로** 들어 있다 — 사람에게 나가는
        길은 `state()` 하나뿐이고 거기서 지운다.
    """
    return await _cached(f"state:{market}:{symbol}", market, lambda: _state_fresh(symbol, market))


def hide_whitelist(payload: dict[str, Any], who: object | None) -> dict[str, Any]:
    """관리자가 아니면 `account.ip_whitelist` 를 비운다 (순수).

    Args:
        payload: `_state_fresh` 결과 (캐시에서 나온 것 — **원본을 고치지 않는다**).
        who: 호출자 (`Caller`) 또는 None.

    Returns:
        관리자면 받은 그대로, 아니면 그 칸만 빈 사본.
    """
    raw = payload.get("account")
    if not isinstance(raw, dict):
        return payload
    account = cast("dict[str, Any]", raw)
    if not account.get("ip_whitelist"):
        return payload
    if getattr(who, "role", None) is Role.ADMIN:
        return payload
    return {**payload, "account": {**account, "ip_whitelist": ""}}


async def _state_fresh(symbol: str = DEFAULT_SYMBOL, market: str = "GATE") -> dict[str, Any]:
    """거래소의 **현재 상태 전부** — 잔고 · 포지션 · 미결 주문 · 조건부 주문.

    Args:
        symbol: 종목 (`BTC_USDT`).
        market: 거래소 (기본 GATE — 2026-08-26 거래소별 콘솔).

    Returns:
        `{balance, position, orders, stops}`.

    Raises:
        HTTPException: 어댑터를 얻을 수 없으면 503.

    Note:
        🔴 **한 번에 다 낸다.** 네 번 물으면 그 사이에 상태가 바뀌어, 화면이 서로
        모순되는 값을 동시에 띄운다 — 포지션은 있는데 증거금은 0 같은 모습이 된다.

        ⚠️ 계좌의 `position_margin` 과 포지션의 `margin` 이 다르게 온다 (2026-08-18
        실측: 계좌 0, 포지션 500.85). **둘 다 낸다** — 감추면 다음에 또 헷갈린다.
    """
    orders = _orders_adapter(market)
    instrument = _instrument(symbol, market)
    # 🔴 거래소가 거절하면(IP 화이트리스트 403 · 밴 · 키 권한) **이유가 화면에 가야 한다**
    #    (규칙 #8).
    #    첫 배포(2026-09-05)에서 테스트넷 키의 화이트리스트에 서버 IP 가 없어 500 만 떴다 —
    #    사람이 "왜" 를 로그에서 찾아야 했다.
    try:
        balance = await orders.get_balance()
        position = await orders.position_snapshot(instrument)
        tracked = await _tracked(orders, market)
        history = await _all_history(orders, market, tracked)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"{market} 거래소 응답: {str(exc)[:300]}") from exc
    try:
        account = await orders.identity()
    except Exception as exc:  # 계정 상세는 부가 정보다 — 키에 Account 권한이 없어도 나머지는 낸다
        account = {"user_id": "", "ip_whitelist": "", "margin_mode": "", "error": str(exc)[:200]}
    margins: dict[str, str] = {}
    if hasattr(orders, "margins"):
        try:
            raw = cast("dict[str, str]", await orders.margins())
            margins = {"total": raw["total"], "order_margin": raw["order_margin"]}
        except Exception:  # 표시용 — 못 읽으면 칸을 비운다 (0 으로 꾸미지 않는다 · 규칙 #8)
            margins = {}
    ledger = await _ledger_side(history.get("history", []))
    # 🔴 **원장을 읽은 뒤에 수익률을 낸다** — 배율이 원장에만 있는 경우가 있다
    #    (판을 지우면 메모리에서 사라진다). 순서가 뒤집히면 그 줄이 전부 빈칸이 된다.
    _apply_returns(history.get("history", []), ledger["plans"])
    return {
        "symbol": symbol,
        "market": market,
        # 🔴 **어느 계정인지 화면이 말한다** (사용자 요구 2026-08-18). 키를 바꿔 끼운 것을
        #    모르면 다른 계정의 잔고를 내 성적으로 읽는다.
        #    ⚠️ 계정 이름은 Gate 가 주지 않는다 — `user_id` 뿐이다. 없는 값을 만들지 않는다.
        "account": account,
        "balance": {
            "broker": balance.broker,
            "available": str(balance.cash),
            "account_position_margin": str(balance.positions_value),
            # ⭐ 총액·대기 주문 증거금 (2026-09-05 · 사용자 요청) — 어댑터가 말할 수 있을 때만.
            **margins,
            # 🔴 **청산 수수료율** (사용자 요구 2026-09-19: *"수수료나 이런것도 같이 적혀서
            #    정확하게 측정됐으면"*). 화면의 "지금 다 닫으면" 이 이 값으로 수수료를 뺀다.
            #
            #    실측 대조(2026-09-19 실계좌 청산): 명목 163.7 x 0.0005 = 0.08185 vs 장부의
            #    실제 수수료 0.081567 — 0.0003 USDT 안쪽이다. 즉 예측 가능한 값이다.
            #
            # ⚠️ **이름이 `_rate` 인 이유**: 값은 **분수**다(0.0005 = 0.05%). `_pct` 로 적었다가
            #    화면이 100 으로 한 번 더 나눠 수수료가 0 이 됐다 — 이름이 단위를 말해야 한다.
            # ⚠️ **테이커다.** 전량 시장가 청산을 가정한다 — 화면 단추(`포지션 전량 청산`)가
            #    시장가이기 때문이다. 지정가로 나가면 메이커(0.0002)라 더 싸고, 그쪽으로 틀리는
            #    것은 안전한 방향이다(예상보다 더 들어온다).
            "taker_rate": str(load_cost_table().for_market(Market(market)).taker),
        },
        "position": position,
        # 🔴 **체결 이력을 함께 낸다** (사용자 신고 2026-08-18: *"주문이 들어갔었는데
        #    거래소 콘솔에서 확인이 안되는 것부터 문제야"*).
        #
        #    미결 주문만 보여 줬는데 **시장가 진입은 즉시 체결돼 미결에 안 남는다** —
        #    나간 흔적이 화면 어디에도 없었다. 포지션은 *지금 상태*이고 이 목록은
        #    *무슨 일이 있었나*다.
        # 🔴 **모든 종목을 낸다** (2026-08-19 사고 ⑨). 이력만 종목 무관이다 —
        #    잔고·포지션·미결은 고른 종목 것이고, *"무슨 일이 있었나"* 는 판 전체다.
        #
        #    콘솔은 `exchange()` 를 기본값(BTC_USDT)으로 부르는데 판은 네 종목에서
        #    돌고 있었다. 그래서 DOGE 가 3시간에 36번 진입하는 동안 화면은
        #    **BTC 의 29건**만 보여 줬고, 사용자가 *"체결 이력이 엄청 적어"* 라고 했다.
        #
        # ⚠️ 종목 수만큼 왕복이 는다. 콘솔은 사람이 보는 화면이라 감당되지만,
        #    판정 경로에서 부르면 안 된다.
        **history,
        # 🔴 **미결·조건부·포지션도 모든 종목이다** (사용자 신고 2026-08-20).
        #
        #    이력만 넓히고(사고 ⑨) 나머지 셋은 고른 종목에 묶어 뒀는데, 콘솔은
        #    `exchange()` 를 **기본값 BTC_USDT** 로만 부른다. 판 6개가 여섯 종목에서
        #    도는 동안 화면은 BTC 만 보고 *"미결 0건 · 조건부 0건"* 이라고 했다.
        #
        #    ⛔ **조건부 0건은 "손절이 없다"로 읽힌다.** 실제로는 SPCX 에 1408 계약과
        #      발동가 139.31 이 멀쩡히 걸려 있었는데, 사람은 무방비라고 읽었다 — 화면이
        #      낼 수 있는 가장 나쁜 거짓말이다 (절대 규칙 #8).
        #
        # ⚠️ 그래도 `position`(고른 종목)은 남긴다 — 상단 카드가 그 값을 쓰고, 없애면
        #    화면이 통째로 바뀐다. 여기서 늘리는 것은 **목록**이다.
        **await _all_live(orders, market, tracked),
        # 🔴 **거래소 이력만으로는 못 읽는 것들** (사용자 요구 2026-08-20).
        #
        #    사용자 요구: *"RUN 에 고유 ID 가 아니라 종목명이 적혔으면 좋겠고, RUN 을
        #    지워도 종목명은 남아있으면 좋겠네"* + *"레버리지도 RUN 이 지워져도 남게"*
        #    + *"주문을 클릭하면 (…) 진입가, 손절가, 1차익절가, 익절"*.
        #
        #    거래소 행에는 **체결가와 실현 손익뿐**이다. 계획(손절선·1차 익절·목표)과
        #    배율은 우리 원장에만 있고, 조인 열쇠는 주문 이름에 박은 매매 id 다 (T18 ⑤).
        #
        # ⭐ **판을 지워도 남는다.** 지우는 것은 `closed_at` 을 찍는 일이라 행이 그대로다.
        **ledger,
    }


async def _ledger_side(rows: list[dict[str, str]]) -> dict[str, Any]:
    """체결 이력에 붙일 **원장 쪽 사실** — 계획 · 시각 · 배율 · 종목.

    Args:
        rows: 거래소 체결 이력.

    Returns:
        `{plans: {매매 id 앞자리: {...}}, runs: {판 표식: {...}}}`. 저장소가 없으면 빈 표.

    Note:
        ⛔ **못 읽어도 이력은 뜬다** (절대 규칙 #8-1). 곁들이는 정보 하나 때문에 *"무슨
        일이 있었나"* 가 통째로 사라지면 그쪽이 훨씬 나쁘다.
    """
    from updown.apps.api.walkforward import ledger_store

    store = ledger_store()
    if store is None:
        return {"plans": {}, "runs": {}}
    texts = [str(row.get("text", "")) for row in rows]
    # 🔴 **조건부 발동은 id 로 잇는다** (사용자 신고 2026-08-20). `ao-{id}` 라는 이름에는
    #    우리 매매 id 가 없어서, 손절이 나면 화면이 *"RUN 미상 · 배율 — · 수익률 —"* 로
    #    떴다 — 원장은 그 매매를 알고 있었는데 화면만 못 이었다.
    owners = await store.stop_owners([text[3:] for text in texts if text.startswith("ao-")])
    wanted = [
        owners.get(text[3:], "") if text.startswith("ao-") else trade_of_text(text)
        for text in texts
    ]
    plans, runs = await asyncio.gather(store.trace(wanted), store.run_tags())
    # ⭐ **화면이 부르는 이름으로도 찾히게 한다.** 화면은 `ao-` 에서 매매 id 를 못 뽑으므로
    #    조건부 id 를 열쇠로 쓴다 — 서버가 그 이름으로 한 벌 더 걸어 준다.
    for text in texts:
        if not text.startswith("ao-"):
            continue
        found = plans.get(owners.get(text[3:], ""))
        if found is not None:
            plans[text] = found
    return {"plans": plans, "runs": runs}


_Live = tuple[list[dict[str, str]], list[dict[str, str]], dict[str, str]]
"""한 종목의 `(미결, 조건부, 포지션)`.

⚠️ 어댑터가 `Any` 라 `asyncio.gather` 결과가 미상으로 번진다 — 이름을 붙여 **경계를
한 곳에서** 좁힌다 (`test_api_admin.fetch` 와 같은 방식).
"""


async def _all_live(
    orders: Any, market: str, tracked: tuple[str, ...]
) -> dict[str, list[dict[str, str]]]:
    """**추적하는 모든 종목**의 미결 주문 · 조건부 · 포지션 (2026-08-20).

    Args:
        orders: 주문 어댑터.
        market: 거래소 — 종목 생성에 쓴다 (2026-08-26).
        tracked: 훑을 종목들 (`_tracked` 파생).

    Returns:
        `{orders, stops, positions}` — 줄마다 `symbol` 이 붙는다.

    Note:
        🔴 **한 종목만 보면 판을 못 본다.** `_all_history` 와 같은 이유이고, 이쪽이
        더 위험하다 — 이력은 *"무슨 일이 있었나"* 지만 조건부는 *"지금 지켜지고 있나"* 다.

        ⚠️ **한 종목이 실패해도 나머지는 낸다.** 하나 때문에 전체가 비면 정작 급한
        것을 못 본다 (절대 규칙 #8-1 과 같은 방향).

        ⚠️ **빈 포지션은 빼고 낸다.** 종목 수만큼 빈 줄이 오면 목록이 잡음이 되고,
        진짜 포지션이 그 안에 묻힌다.

        ⭐ 동시에 묻는다. 순차로 하면 종목 수만큼 곱해진다.
    """

    async def one(symbol: str) -> _Live:
        """종목 하나의 미결·조건부·포지션을 동시에 묻는다.

        Args:
            symbol: 종목.

        Returns:
            `(미결 주문, 조건부, 포지션)`. 한 축이 실패하면 그 축만 비운다 — 다른 축을 버리지
            않는다.
        """
        instrument = _instrument(symbol, market)
        # ⚠️ 어댑터가 `Any` 라 `gather` 결과가 미상으로 번진다 — **경계를 여기서 좁힌다**
        #    (`test_api_admin.fetch` 와 같은 방식). 호출부마다 무시 주석을 다는 것은
        #    경고를 끄는 것이고 이쪽은 타입을 붙이는 것이다.
        got = cast(
            "list[Any]",
            await asyncio.gather(
                orders.open_orders(instrument),
                orders.open_stops(instrument),
                orders.position_snapshot(instrument),
                return_exceptions=True,
            ),
        )
        rows, resting, held = got[0], got[1], got[2]
        return (
            [] if isinstance(rows, BaseException) else rows,
            [] if isinstance(resting, BaseException) else resting,
            {} if isinstance(held, BaseException) else held,
        )

    done = await asyncio.gather(*(one(symbol) for symbol in tracked), return_exceptions=True)
    open_orders: list[dict[str, str]] = []
    stops: list[dict[str, str]] = []
    positions: list[dict[str, str]] = []
    for symbol, got_one in zip(tracked, done, strict=True):
        if isinstance(got_one, BaseException):
            _logger.warning(
                "console_live_failed", payload={"symbol": symbol, "error": str(got_one)[:140]}
            )
            continue
        rows, resting, held = got_one
        open_orders += [{**row, "symbol": symbol} for row in rows]
        stops += [{**row, "symbol": symbol} for row in resting]
        if held:
            positions.append({**held, "symbol": symbol})
    return {"orders": open_orders, "stops": stops, "positions": positions}


async def _all_history(
    orders: object, market: str, tracked: tuple[str, ...]
) -> dict[str, list[dict[str, str]]]:
    """**추적하는 모든 종목**의 체결·청산 이력 (2026-08-19 사고 ⑨).

    Args:
        orders: 주문 어댑터.
        market: 거래소 — 종목 생성에 쓴다 (2026-08-26).
        tracked: 훑을 종목들 (`_tracked` 파생).

    Returns:
        `{history, closes}` — 최근이 앞이고 줄마다 `symbol` 이 붙는다.

    Note:
        🔴 **한 종목만 보면 판을 못 본다.** 콘솔은 여러 RUN 을 한 화면에 놓는데
        이력만 기본 종목(BTC)에 묶여 있었다.

        ⚠️ **한 종목이 실패해도 나머지는 낸다** — 하나 때문에 전체가 비면 정작 급한
        것을 못 본다 (절대 규칙 #8-1 과 같은 방향).

        ⭐ 동시에 묻는다. 순차로 하면 종목 수만큼 곱해진다.
    """

    async def one(symbol: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """종목 하나의 끝난 주문(손익 붙임)과 실현 손익 이력.

        Args:
            symbol: 종목.

        Returns:
            `(주문 이력, 실현 손익)`. 이력을 못 읽으면 이력만 비우고 실현 손익은 남긴다.
        """
        instrument = _instrument(symbol, market)
        closes = await _closes(orders, instrument)
        lot = await _lot(orders, instrument)
        try:
            rows = cast(
                "list[dict[str, str]]",
                await orders.recent_orders(instrument),  # type: ignore[attr-defined]
            )
        except Exception as exc:
            _logger.warning(
                "console_history_unreadable",
                payload={"symbol": symbol, "error": str(exc)[:140]},
            )
            return [], closes
        return _with_money(rows, closes, lot), closes

    done = await asyncio.gather(*(one(symbol) for symbol in tracked), return_exceptions=True)
    history: list[dict[str, str]] = []
    closes: list[dict[str, str]] = []
    for symbol, got in zip(tracked, done, strict=True):
        if isinstance(got, BaseException):
            _logger.warning(
                "console_symbol_failed", payload={"symbol": symbol, "error": str(got)[:140]}
            )
            continue
        rows, shut = got
        history += [{**row, "symbol": symbol} for row in rows]
        closes += [{**row, "symbol": symbol} for row in shut]
    # ⚠️ 시각이 없는 줄은 뒤로 보낸다 — 지어낸 값으로 정렬하면 순서가 거짓말한다.
    history.sort(key=lambda row: str(row.get("create_time", "")), reverse=True)
    closes.sort(key=lambda row: str(row.get("time", "")), reverse=True)
    return {"history": history, "closes": closes}


_CLOSES_TTL_S = 120.0
"""종목별 청산 이력 캐시 — `(읽은 시각, 행들)`.

🔴 T217 (2026-09-04): 이 호출이 바이낸스 `income` 이라 **weight 30** 인데 콘솔 `/exchange/state`
가 종목마다 20초에 한 번씩 불러 6종목 = 분당 540 — 한도(2400)의 22% 를 청산 이력 하나가
먹었다. 청산 이력은 분 단위로 안 바뀐다. 실패는 캐시하지 않는다 (빈 목록을 2분간 굳히지 않게).
"""
_CLOSES_CACHE = TtlCache[list[dict[str, str]]]("exchange.closes", _CLOSES_TTL_S)


async def _closes(orders: object, instrument: Instrument) -> list[dict[str, str]]:
    """청산 손익 — 못 읽으면 빈 목록. 120초 캐시.

    Args:
        orders: 주문 어댑터.
        instrument: 대상 종목.

    Returns:
        `{time, pnl, side, ...}` 목록.

    Note:
        ⚠️ **실패해도 화면을 막지 않는다.** 손익은 곁들이는 값이고, 이것 때문에 포지션·
        미결 주문이 안 보이면 정작 급한 것을 못 본다.
    """
    if not hasattr(orders, "position_closes"):
        return []
    market = getattr(getattr(instrument, "market", None), "value", instrument.market)
    key = f"{market}:{instrument.symbol}"
    kept = _CLOSES_CACHE.fresh(key)
    if kept is not None:
        return kept[1]
    stale = _CLOSES_CACHE.peek(key)
    try:
        rows = cast("list[dict[str, str]]", await orders.position_closes(instrument))  # type: ignore[attr-defined]
    except Exception as exc:
        _logger.warning("console_closes_unreadable", payload={"error": str(exc)[:140]})
        return stale[1] if stale is not None else []
    return _CLOSES_CACHE.put(key, rows)


LOT_TTL_S = 6 * 3600.0
_LOTS = TtlCache[Decimal]("exchange.lots", LOT_TTL_S)
"""종목별 계약 승수 — **6시간에 한 번** 묻는다 (T269 #3 · 전에는 TTL 없는 손캐시).

⚠️ 콘솔은 4초마다 폴링하고 종목이 9개다. 매번 명세를 물으면 하루 20만 번이 되는데,
승수는 계약이 상장돼 있는 한 **안 바뀌는 값**이다.

⛔ 못 읽으면 캐시에 넣지 않는다 — 0 을 심으면 수익률이 영영 안 나온다.
"""


async def _lot(orders: Any, instrument: Any) -> Decimal:
    """계약 승수 (1계약이 몇 코인인가) — 증거금을 계산하는 데 필요하다."""
    symbol = str(instrument.symbol)
    kept = _LOTS.get(symbol)
    if kept is not None:
        return kept
    try:
        spec = await orders.contract_spec(instrument)
    except Exception:
        # ⛔ 못 읽어도 이력은 뜬다 — 수익률 칸만 빈다 (§1.2.1).
        return Decimal(0)
    got = Decimal(str(spec.get("quanto_multiplier", "0")))
    if got > 0:
        _LOTS.put(symbol, got)
    return got


def _returns(close: dict[str, str], lot: Decimal) -> dict[str, str]:
    """청산 한 줄에서 **가격이 얼마나 움직였고 명목이 얼마였나** (사용자 신고 2026-08-20).

    Args:
        close: 거래소 청산 기록 (`side` · `long_price` · `short_price` · `max_size` · `pnl`).
        lot: 계약 승수.

    Returns:
        `{move_pct, notional}`. 못 재면 빠진다.

    Note:
        ⚠️ **배율을 안 받는다.** 여기서는 아직 모른다 — 지운 판의 배율은 메모리에 없고
        원장에만 있어서, 수익률(`gain_pct`)은 원장을 읽은 뒤에 낸다 (`_apply_returns`).

    Note:
        🔴 사용자 신고: *"이거는 실현 손익이 마이너스인데, 어떻게 돈을 번거야?"*

        둘 다 사실이었다 — **가격으로는 이겼고 수수료로 졌다.** 실측 (ETH 숏 85계약):

        ```
        진입 2316.1753  청산 2314.5500
        가격 변동         +0.0702%
        배율 20배를 곱해   +1.40%    ← 수수료 전
        거래소 실현        -0.5866 USDT = -0.60%   ← 수수료 포함
        ```

        ⇒ **화면에는 수수료 포함(`gain_pct`)을 놓는다.** 옆 칸이 거래소 실현 손익인데
          수수료 전 값을 놓으면 부호가 갈려 사람이 모순으로 읽는다.
        ⇒ 수수료 전 값은 버리지 않는다 — 둘의 차이가 곧 **비용이 얼마나 먹었나**이고,
          20배에서 그 차이가 증거금의 2%p 다.

        ⭐ **거래소 자기 숫자만 쓴다.** `config/costs.yml` 의 실측 요율로 보정하면
        *모형*을 *사실* 칸에 섞는 것이 된다.
    """
    size = abs(Decimal(close.get("max_size") or "0"))
    long_at = Decimal(close.get("long_price") or "0")
    short_at = Decimal(close.get("short_price") or "0")
    if size <= 0 or long_at <= 0 or short_at <= 0 or lot <= 0:
        return {}
    # ⭐ **차익의 분자는 방향과 무관하다** — 롱은 long_at 에 사서 short_at 에 팔고,
    #    숏은 short_at 에 팔아 long_at 에 산다. 다른 것은 **무엇이 진입가인가**뿐이다.
    entry = long_at if close.get("side") == "long" else short_at
    return {
        "move_pct": f"{(short_at - long_at) / entry * 100:.4f}",
        "notional": f"{entry * size * lot:.4f}",
    }


def _apply_returns(rows: list[dict[str, str]], plans: dict[str, Any]) -> None:
    """**배율을 원장에서 채우고** 수익률을 마저 낸다 (사용자 요구 2026-08-20).

    Args:
        rows: 체결 이력 — 제자리에서 고친다.
        plans: 매매 id 앞자리 → 원장 계획.

    Note:
        🔴 사용자 요구: *"레버리지도 RUN 이 지워져도 남게 하고싶어."*

        `leverage_of` 는 **메모리에 살아 있는 판만** 본다. 판을 지우면 빈칸이 되고,
        그러면 수익률도 못 낸다 — 배율이 있어야 증거금을 알기 때문이다.

        ⭐ **원장의 매매별 배율이 더 정확하다.** 판이 도중에 배율을 바꿨으면 판 값은
        *지금* 값이고, `wf_trades.leverage` 는 *그 매매의* 값이다.

        ⚠️ 그래서 이 계산이 원장을 읽은 **뒤에** 온다. 순서가 뒤집히면 지운 판의 줄이
        전부 빈칸이 된다.
    """
    for row in rows:
        text = str(row.get("text", ""))
        # 🔴 **화면과 같은 규칙으로 찾는다** (사용자 신고 2026-08-20).
        #
        #    `trade_of_text("ao-...")` 는 빈 문자열이다 — 조건부 발동은 Gate 가 만든
        #    이름이라 매매 id 가 없기 때문이다. 그래서 `_ledger_side` 가 조건부 주문
        #    id 로 매매를 되찾아 **그 이름으로도** 계획을 걸어 둔다.
        #
        #    ⚠️ 여기서 그 이름을 안 보면 손절 발동 줄만 배율을 못 찾고, 배율이 없으면
        #    증거금을 몰라 **수익률이 통째로 빈칸**이 된다 — 실측:
        #
        #      ao-2090441265442193408   pnl -25.068  notional 3057  gain **없음**
        #      t-6adb7ca9ce0b-cl-0      pnl  -8.276  notional 1269  gain -13.05
        #
        #    화면은 `plan?.leverage` 로 배율을 찾아 20x 를 띄우고 있었으므로, 같은 줄에
        #    **배율은 있는데 수익률만 없는** 모양이 됐다.
        plan = plans.get(text) or plans.get(trade_of_text(text))
        if plan and plan.get("leverage"):
            # ⭐ 원장 값이 이긴다 — 그 매매가 실제로 굴린 배율이다.
            row["leverage"] = str(plan["leverage"])
        lever = Decimal(row.get("leverage") or "0")
        notional = Decimal(row.get("notional") or "0")
        raw = row.get("pnl") or ""
        if lever > 0 and notional > 0 and raw:
            margin = notional / lever
            row["gain_pct"] = f"{Decimal(raw) / margin * 100:.4f}"


def _with_money(
    history: list[dict[str, str]], closes: list[dict[str, str]], lot: Decimal
) -> list[dict[str, str]]:
    """체결 줄에 **실현 손익 · 수익률 · 배율**을 얹는다 (사용자 요구 2026-08-19).

    Args:
        history: 주문 이력.
        closes: 청산 이력.
        lot: 계약 승수 — 증거금을 계산해 수익률을 내는 데 쓴다.

    Returns:
        `pnl`·`gain_pct`·`move_pct`·`leverage` 가 더해진 이력.

    Note:
        🔴 **주문 이름으로 잇는다** (사용자 신고 2026-08-20: *"실현 손익이 마이너스인데
        어떻게 돈을 번거야?"*).

        예전에는 **시각 근접(±5분)** 으로 붙였다. 그런데 양쪽에 `text` 라는 **정확한
        열쇠**가 있었고, 근접으로 고르니 남의 손익이 붙었다 — 실측:

        ```
        주문 20:51:10  t-2a69a9715081-cl-0   붙은 손익 -9.1037   🔴
        청산 20:51:10  t-2a69a9715081-cl-0   진짜 손익 -0.5866
        청산 20:48:00  t-2904cb3cd6dd-cl-0        손익 -9.1037   ← 여기서 왔다
        ```

        ⛔ **틀린 손익은 빈칸보다 나쁘다.** 3분 떨어진 남의 매매 결과를 이 줄의 성적으로
        읽게 되고, 그 줄이 통째로 거짓이 된다 (절대 규칙 #8).

        ⚠️ **시각 대조는 남긴다** — `liq-`(강제청산)처럼 우리 이름이 없는 청산이 있고,
        그때까지 빈칸으로 두면 정작 제일 알아야 할 줄이 빈다. 다만 **이름으로 먼저
        맞춘 뒤 남은 것끼리만** 본다.

        ⛔ **배율은 우리 기록에서 온다.** 거래소 주문 줄에도 청산 줄에도 배율이 없고,
        지금 포지션의 배율을 붙이면 **그때 값이 아니라 지금 값**이 된다.
    """
    from updown.apps.api.walkforward import leverage_of

    by_text = {str(row.get("text", "")): row for row in closes if row.get("text")}
    taken: set[int] = set()
    # ⭐ **이름으로 먼저 짝을 짓는다.** 그러고 남은 청산만 시각 대조에 넘긴다.
    named = {
        index
        for index, row in enumerate(closes)
        if str(row.get("text", "")) in {str(item.get("text", "")) for item in history}
    }
    out: list[dict[str, str]] = []
    for row in history:
        made = dict(row)
        text = str(row.get("text", ""))
        lever = leverage_of(text)
        made["leverage"] = lever
        if row.get("is_reduce_only") != "True":
            out.append(made)
            continue
        close = by_text.get(text)
        if close is None and row.get("finish_time"):
            when = float(row["finish_time"])
            best: tuple[float, int] | None = None
            for index, item in enumerate(closes):
                if index in taken or index in named or not item.get("time"):
                    continue
                away = abs(float(item["time"]) - when)
                # ⚠️ 5분을 넘겨 붙이지 않는다 — 옛 청산이 새 체결에 붙으면 그 줄이
                #    통째로 거짓이 된다.
                if away <= 300 and (best is None or away < best[0]):
                    best = (away, index)
            if best is not None:
                taken.add(best[1])
                close = closes[best[1]]
                # 🔴 **이름으로 못 이은 줄임을 남긴다** — 추정으로 붙인 값이다.
                made["pnl_guessed"] = "true"
        if close is not None:
            made["pnl"] = str(close.get("pnl", ""))
            made.update(_returns(close, lot))
        out.append(made)
    return out


@router.post("/close")
async def close(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """포지션을 **시장가로 전량** 닫는다.

    Args:
        payload: `{symbol, market?}` — market 기본 GATE (2026-08-26).

    Returns:
        주문 결과 + 닫은 뒤의 상태.

    Raises:
        HTTPException: 포지션이 없으면 409, 어댑터가 없으면 503.

    Note:
        🔴 **`size=0` + `close=true`** 로 보낸다 — 수량을 우리가 계산하면 부분체결·펀딩으로
        어긋난 만큼이 **반대 포지션**으로 열린다. 거래소가 세는 것이 정확하다.

        ⚠️ 멱등키에 시각을 넣는다. 같은 키로 두 번 보내면 거래소가 두 번째를 거부하는데,
        사용자가 "닫기" 를 두 번 누르는 것은 정상 행동이다 (첫 번째가 실패했을 수 있다).
    """
    symbol = str(payload.get("symbol", DEFAULT_SYMBOL))
    market = str(payload.get("market", "GATE"))
    orders = _orders_adapter(market)
    instrument = _instrument(symbol, market)
    held = await orders.position_snapshot(instrument)
    if not held:
        raise HTTPException(409, f"{symbol} 에 열린 포지션이 없다 — 닫을 것이 없다")
    # ⚠️ **30자 제한이 있다** (`TEXT_LIMIT`). 종목명을 넣으면 넘쳐서 해시로 접히고,
    #    그러면 사람이 로그에서 읽을 수 없다 — 짧게 만든다.
    key = f"cclose-{int(time.time())}"
    result = await orders.close_position(instrument, key)
    _logger.info(
        "console_position_closed",
        payload={
            "symbol": symbol,
            "contracts": held.get("size"),
            "margin": held.get("margin"),
            "status": result.status.value,
        },
    )
    return {
        "status": result.status.value,
        "broker_order_id": result.broker_order_id,
        "closed": held,
        "state": await _state_fresh(symbol, market),
    }


@router.get("/escape")
async def escape_view(symbol: str = DEFAULT_SYMBOL, market: str = "GATE") -> dict[str, Any]:
    """**여기서 나가려면 얼마에 걸어야 하나** — 계산만 하고 주문은 안 낸다.

    Args:
        market: 거래소 (기본 GATE — 2026-08-26 거래소별 콘솔).
        symbol: 종목.

    Returns:
        `{plan}` 또는 포지션이 없으면 `{plan: None}`.

    Raises:
        HTTPException: 어댑터가 없으면 503.

    Note:
        🔴 **값을 기계가 정하지 않는다** (사용자 확정 2026-08-20). 화면이 *"이 값이면
        이만큼 실현된다"* 를 나란히 놓고, 거는 것은 사람이 누른다 — 실측이 그 이유다:

        ```
        SPCX 를 그때 시장가로 던졌다면   -420
        표시가 아래 지정가로 기다렸더니   -74   (15시간 31분)
        ```

        ⚠️ **수수료는 안 뺀다.** 명세의 요율은 기준값이고 실제 요율은 주문 응답에만 온다.
    """
    orders = _orders_adapter(market)
    instrument = _instrument(symbol, market)
    held = await orders.position_snapshot(instrument)
    if not held:
        return {"plan": None, "why": f"{symbol} 에 열린 포지션이 없다"}
    spec = await orders.contract_spec(instrument)
    book = await orders.book_here(instrument)
    plan = escape_plan(symbol, spec, book, held)
    if plan is None:
        return {"plan": None, "why": "표시가나 포지션을 못 읽었다 — 값을 지어내지 않는다"}
    return {
        "plan": {
            "symbol": plan.symbol,
            "side": "롱" if plan.long else "숏",
            "size": plan.size,
            "entry": str(plan.entry),
            "mark": str(plan.mark),
            "bid": str(plan.bid or ""),
            "ask": str(plan.ask or ""),
            "limit": str(plan.limit),
            "suggested": str(plan.suggested),
            "realized": float(plan.realized),
            "touch": str(plan.touch or ""),
            "touch_ok": plan.touch_ok,
            "touch_realized": None if plan.touch_realized is None else float(plan.touch_realized),
            # ⭐ 참이면 화면이 이 창을 **안 그린다** — 그냥 전량 청산하면 되는 상황이다.
            "market_ok": plan.market_ok,
        }
    }


@router.post("/escape")
async def escape_place(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """**나갈 지정가를 건다** — 사람이 값을 확인하고 누른 것만 나간다.

    Args:
        payload: `{symbol, price}`.

    Returns:
        주문 결과 + 건 뒤의 상태.

    Raises:
        HTTPException: 포지션이 없으면 409, 값이 규칙 밖이면 400, 어댑터가 없으면 503.

    Note:
        🔴 **`OrderKind.CLOSE` 로만 나간다** — 어댑터가 그것을 보고 `reduce_only` 를
        붙인다. 빼면 **반대 포지션이 새로 열려** 위험이 두 배가 된다. 이 함수에는
        포지션을 늘릴 수 있는 경로가 한 줄도 없어야 한다 (수량도 방향도 거래소가 말한
        보유에서 그대로 뒤집어 만든다).

        🔴 **한계가를 서버가 다시 본다.** 화면이 계산한 값을 그대로 믿으면, 화면이
        낡았을 때 거래소가 거절하고 그 실패는 *"주문 0건"* 으로만 보인다. 여기서 막고
        **왜 막혔는지를 말한다** (절대 규칙 #8).

        ⚠️ 멱등키에 시각을 넣는다 — 사람이 두 번 누르는 것은 정상 행동이다 (첫 번째가
        실패했을 수 있다).
    """
    symbol = str(payload.get("symbol", DEFAULT_SYMBOL))
    market = str(payload.get("market", "GATE"))
    orders = _orders_adapter(market)
    instrument = _instrument(symbol, market)
    held = await orders.position_snapshot(instrument)
    if not held:
        raise HTTPException(409, f"{symbol} 에 열린 포지션이 없다 — 나갈 것이 없다")
    spec = await orders.contract_spec(instrument)
    book = await orders.book_here(instrument)
    plan = escape_plan(symbol, spec, book, held)
    if plan is None:
        raise HTTPException(409, "표시가나 포지션을 못 읽었다 — 값을 지어내지 않는다")

    raw = payload.get("price")
    price = plan.suggested if raw in (None, "") else Decimal(str(raw))
    # 🔴 **한계 밖이면 여기서 막는다.** 거래소에 보내면 `PRICE_TOO_DEVIATED` 가 오고,
    #    그 실패는 화면에서 "주문 0건" 으로만 보인다.
    if (price < plan.limit) if plan.long else (price > plan.limit):
        raise HTTPException(
            400,
            f"{price} 는 거래소가 안 받는다 — 표시가 {plan.mark} 에서 너무 멀다. "
            f"{'최저' if plan.long else '최고'} {plan.limit} 까지만 걸 수 있다 "
            "(Gate 규칙 `deviation-rate limit`)",
        )

    order = OrderRequest(
        instrument=instrument,
        # ⭐ 방향은 **보유의 반대**다 — 우리가 고르는 것이 아니라 뒤집는 것이다.
        side=Side.SELL if plan.long else Side.BUY,
        # 🔴 이 값이 `reduce_only` 를 만든다. 다른 종류로 바꾸면 포지션이 열린다.
        order_kind=OrderKind.CLOSE,
        order_type=OrderType.LIMIT,
        quantity=Decimal(plan.size),
        price=price,
        idempotency_key=f"esc-{int(time.time())}",
        # ⚠️ 판정에서 나온 주문이 아니다 — **사람이 콘솔에서 누른 것**이라 가리킬
        #    `ApprovedOrder` 가 없다. 빈 문자열이 그 사실이고, 지어내지 않는다.
        approved_order_id="",
        leg_index=1,
        revision_id=None,
    )
    result = await orders.submit_order(order)
    _logger.info(
        "console_escape_placed",
        payload={
            "symbol": symbol,
            "side": "롱" if plan.long else "숏",
            "contracts": plan.size,
            "price": str(price),
            "mark": str(plan.mark),
            "realized_before_fees": str(
                (price - plan.entry) * Decimal(plan.size if plan.long else -plan.size)
            ),
            "status": result.status.value,
            "note": "사람이 값을 확인하고 누른 탈출 지정가다 — reduce_only",
        },
    )
    return {
        "status": result.status.value,
        "broker_order_id": result.broker_order_id,
        "price": str(price),
        "state": await _state_fresh(symbol, market),
    }


@router.delete("/orders/{order_id}")
async def cancel(
    order_id: str, symbol: str = DEFAULT_SYMBOL, market: str = "GATE"
) -> dict[str, Any]:
    """미결 지정가 주문 하나를 거둔다.

    Args:
        market: 거래소 (기본 GATE — 2026-08-26 거래소별 콘솔).
        order_id: 거래소 주문 id.
        symbol: 종목.

    Returns:
        거둔 뒤의 상태.

    Raises:
        HTTPException: 어댑터가 없으면 503.
    """
    orders = _orders_adapter(market)
    from updown.execution.binance_paper import BinancePaperAdapter

    if isinstance(orders, BinancePaperAdapter):
        # ⚠️ BN 취소는 심볼이 필수다 — 어댑터가 발주 안 한 주문은 심볼 기억이 없다.
        await orders.cancel_order(order_id, symbol=symbol.replace("_", ""))
    else:
        await orders.cancel_order(order_id)
    _logger.info("console_order_cancelled", payload={"order_id": order_id, "symbol": symbol})
    return await _state_fresh(symbol, market)


@router.delete("/stops/{stop_id}")
async def cancel_stop(
    stop_id: str, symbol: str = DEFAULT_SYMBOL, market: str = "GATE"
) -> dict[str, Any]:
    """조건부(스탑) 주문 하나를 거둔다.

    Args:
        market: 거래소 (기본 GATE — 2026-08-26 거래소별 콘솔).
        stop_id: 거래소 조건부 주문 id.
        symbol: 종목.

    Returns:
        거둔 뒤의 상태.

    Raises:
        HTTPException: 어댑터가 없으면 503.

    Note:
        ⚠️ **포지션을 들고 있는데 손절을 거두면 손절 없는 포지션이 된다.** 화면이 경고를
        띄우지만 막지는 않는다 — 사람이 정리하려고 누르는 경우가 있고(갈아 끼우기),
        여기서 막으면 앱에서 정리할 방법이 다시 없어진다.
    """
    orders = _orders_adapter(market)
    await orders.cancel_stop(stop_id)
    _logger.warning(
        "console_stop_cancelled",
        payload={
            "stop_id": stop_id,
            "symbol": symbol,
            "note": "포지션이 있으면 손절 없는 상태가 된다 — 사람이 누른 것이다",
        },
    )
    return await _state_fresh(symbol, market)


TRACKED = (
    "BTC_USDT",
    "ETH_USDT",
    "SOL_USDT",
    "XRP_USDT",
    "DOGE_USDT",
    # 🔴 **토큰화 주식** (사용자 요청 2026-08-19). 코인과 성질이 다르다 —
    #    수수료가 1.5배(테이커 0.075%)이고, 기초자산 장이 닫힌 시간에는 호가가 얇다.
    #
    # ⚠️ **SNDK·SKHY 는 testnet 에 없다.** 순위·시세에는 뜨지만 판을 띄우면 막힌다 —
    #    감추지 않는 이유는 *"왜 이건 못 띄우나"* 가 화면에서 보여야 하기 때문이다.
    "TSLAX_USDT",
    "SPCX_USDT",
    "SNDK_USDT",
    "SKHY_USDT",
)
"""순위 창이 보여 주는 종목들 (T18 ② · 사용자 확정 — 큰 것 넷으로 시작).

⚠️ **여기 늘리는 것과 판을 띄우는 것은 다른 일이다.** 이 목록은 *보는* 것이고, 판을
띄우려면 `config/costs.yml` 의 `spec_ticks` 에도 선언돼 있어야 한다 (T18 ①).

⛔ 실매매는 BTC 만이다 (사용자 확정). 나머지 종목의 비용·데이터 부재는 감수하되,
그 사실이 화면에서 보이지 않으면 안 된다.
"""


@router.get("/symbols")
async def symbols() -> dict[str, Any]:
    """**띄울 수 있는 종목 목록** — 화면의 고르개가 쓴다 (2026-08-20).

    Returns:
        `{rows: [{symbol, label, tradable}]}`.

    Note:
        🔴 **화면에 박아 두면 종목을 추가해도 안 뜬다** (사용자 신고 2026-08-20).
        `costs.yml` 과 `TRACKED` 에 넷을 넣었는데 고르개에는 다섯 개 그대로였다 —
        목록이 **두 벌**이었고 한쪽만 고쳤기 때문이다.

        ⇒ 단일 출처는 `TRACKED` 이고, *"띄울 수 있나"* 는 `costs.yml` 의 호가 눈금
        선언 여부가 정한다.

        ⚠️ **`tradable=false` 도 목록에 남긴다.** 빼면 *"왜 이 종목이 없지"* 에 화면이
        답을 못 한다 — 못 띄우는 이유가 보여야 사람이 고칠 수 있다 (절대 규칙 #8).

        ⚠️ **주문 가능 여부는 여기서 안 본다.** 그것은 거래소에 계약을 물어야 알고
        (SNDK·SKHY 는 라이브에 있고 testnet 에 없다), 종목당 한 번씩 왕복이 든다 —
        판을 띄우는 순간 문 앞에서 걸러진다 (`_live_start`).
    """
    return {
        "rows": [
            {
                "symbol": symbol,
                # ⭐ 이름은 심볼에서 만든다. 따로 표를 두면 그것도 갱신 대상이 된다.
                "label": f"{symbol.removesuffix('_USDT')} 무기한",
                "tradable": symbol in _TRADABLE,
            }
            for symbol in TRACKED
        ]
    }


@router.get("/ranking")
async def ranking() -> dict[str, Any]:
    """**오늘 어느 종목이 거래하기 좋은가** — 나란히 놓기만 한다 (T18 ②).

    Returns:
        `{rows: [{symbol, price, turnover, volatility, spread, change}], at}`.

    Raises:
        HTTPException: 조회 어댑터가 없으면 503.

    Note:
        🔴 **표시 전용이다.** 가중합 점수도 "오늘의 1위" 배지도 만들지 않는다 (사용자
        확정 2026-08-19). 만드는 순간 그것이 **검증되지 않은 새 규칙**이 되고, 화면에서
        추천처럼 보인다 — 이 프로젝트는 규칙을 out-of-sample 로만 채택한다 (§5.6.7).

        ⇒ 정렬은 화면이 한다. 사람이 어느 열로 볼지 고르는 것까지가 여기의 일이다.

        ⭐ **한 번의 호출로 모두 받는다.** 종목마다 부르면 행마다 다른 순간의 값이라
        비교가 성립하지 않는다.

        ⚠️ **스프레드는 지금 순간의 1호가 차이**다. 24시간 평균이 아니므로 조용한
        시간대에 재면 좁게 나온다 — 비용 판단의 근거로 쓰지 않는다 (그것은
        `config/costs.yml` 의 실측이다).
    """
    provider = MarketDataProvider()
    if Market.GATE.value not in provider.live_markets():
        # 이 창은 Gate 전용이다. Gate 가 없는 API(로컬 실계좌 모드 · UPDOWN_MARKETS=NONE)에서는
        # 503 대신 빈 표 — 콘솔이 "연결 없음" 을 말하는 자리는 상단 카드다.
        return {"rows": [], "at": datetime.now(UTC).isoformat(), "note": "연결된 Gate 계정이 없다"}
    quotes = provider.adapter_for(Market.GATE)
    if not isinstance(quotes, GateAdapter):  # pragma: no cover - 제공자가 이미 본다
        raise HTTPException(503, "Gate 조회 어댑터가 없다")
    rows = await quotes.tickers()
    found = {row.get("contract", ""): row for row in rows}
    # 🔴 **나갈 수 있는지를 고르기 전에 보여 준다** (사용자 제안 2026-08-20:
    #    *"애초에 유동성이 적은 종목은 종목 선택 시 유의가 뜨는 로직을 만드는 건 어때?"*).
    #
    #    이 창이 지금까지 거짓말을 하고 있었다 — 위 `tickers()` 는 **조회용 라이브 API**
    #    의 값이라 SPCX 가 스프레드 0.007% 로 BTC 급으로 건강해 보인다. 증거금 420 을
    #    15시간 묶은 바로 그 계약이다 (주문 거래소 실측: 매수 격차 21.8% · 깊이 0).
    #
    # ⚠️ **명목을 0 으로 넘긴다.** 이 창에서는 아직 예산이 정해지지 않았으므로 깊이를
    #    판정할 근거가 없다 — 격차만 판정하고 깊이는 **숫자로만** 보여 준다.
    orders = _orders_adapter()
    books = dict(
        zip(
            TRACKED,
            await asyncio.gather(
                *(probe_book(orders, _instrument(name), Decimal(0)) for name in TRACKED),
                return_exceptions=True,
            ),
            strict=True,
        )
    )
    # ⭐ **최근 1시간 폭** (사용자 요구 2026-08-20). 24시간 변동성으로는 *"지금 어느
    #    종목이 움직이나"* 를 못 본다 — ETH 17.58% 와 XRP 18.18% 가 거의 같아 보이는데,
    #    최근 3시간은 2.83% 대 6.87% 였고 **매매가 난 것은 XRP 뿐**이었다.
    spans = dict(
        zip(
            TRACKED,
            await asyncio.gather(
                *(_recent(quotes, name) for name in TRACKED), return_exceptions=True
            ),
            strict=True,
        )
    )
    out: list[dict[str, Any]] = []
    for symbol in TRACKED:
        row = found.get(symbol)
        if row is None:
            # ⛔ 조용히 건너뛰지 않는다 — 목록에서 사라지면 아무도 못 알아챈다 (규칙 #8).
            out.append({"symbol": symbol, "missing": "거래소가 이 계약을 안 준다"})
            continue
        last = _decimal(row.get("last"))
        high = _decimal(row.get("high_24h"))
        low = _decimal(row.get("low_24h"))
        bid = _decimal(row.get("highest_bid"))
        ask = _decimal(row.get("lowest_ask"))
        out.append(
            {
                "symbol": symbol,
                "price": None if last is None else float(last),
                # 거래대금 — 24시간 결제통화 기준.
                "turnover": _float(row.get("volume_24h_quote")),
                # 변동성 — 24시간 고저폭을 현재가로 나눈 비율(%).
                "volatility": (
                    None
                    if last is None or high is None or low is None or last <= 0
                    else float((high - low) / last * 100)
                ),
                # 스프레드 — 지금 1호가 차이(%). ⚠️ 24시간 평균이 아니다.
                "spread": (
                    None
                    if bid is None or ask is None or ask <= 0
                    else float((ask - bid) / ask * 100)
                ),
                "change": _float(row.get("change_percentage")),
                # 🔴 **판을 띄울 수 있는 종목인가** (T18 ①·④). 못 띄우는 종목이 있는
                #    것은 받아들이되, 조용히 넘어가지 않는다.
                #
                # ⚠️ 이것은 **설정 질문**이다 — 우리가 눈금을 아는가. 시장이 살아 있는지도
                #    주문 거래소에 계약이 있는지도 안 본다. 그래서 아래 `exit` 이 따로 있고,
                #    둘을 하나로 합치지 않는다 (합치면 왜 막혔는지가 사라진다).
                "tradable": symbol in _TRADABLE,
                "exit": _exit_view(books.get(symbol)),
                # ⭐ **지금 움직이고 있나** — 자리가 나는 빈도는 거래량이 아니라 여기서 온다.
                #    ⛔ 못 읽으면 None 이다. 0 으로 채우면 *"안 움직인다"* 로 읽힌다.
                "recent_pct": (got if isinstance((got := spans.get(symbol)), float) else None),
            }
        )
    return {"rows": out, "at": datetime.now(UTC).isoformat()}


RECENT_MINUTES = 60
"""**최근 폭**을 재는 창 (사용자 요구 2026-08-20).

🔴 24시간 변동성은 *"오늘 어느 종목에 판을 띄울까"* 에 답을 못 한다. 실측:

```
ETH  변동성(24h) 17.58%  최근 3시간 2.83%   매매 0
XRP  변동성(24h) 18.18%  최근 3시간 6.87%   매매 많음
```

24시간으로는 둘이 같아 보이는데 **실제로 자리가 난 것은 XRP 뿐**이었다.

⚠️ 박스권 매매는 **가격이 박스 끝에 닿아야** 발동한다. 거래량이 아무리 많아도 가격이
가만히 있으면 자리가 안 난다 — BTC 는 거래대금이 XRP 의 36배인데 3시간에 1.67%
움직였고 매매가 0 이었다.
"""


async def _recent(quotes: Any, symbol: str) -> float | None:
    """**최근 1시간 고저폭**(%) — 지금 이 종목이 움직이고 있나.

    Args:
        quotes: 조회 어댑터.
        symbol: 종목.

    Returns:
        폭(%). 못 읽으면 None — **0 으로 채우지 않는다** (0 은 "안 움직인다" 로 읽힌다).

    Note:
        ⭐ **조회용 라이브 API 를 본다.** 이것은 *"시장이 얼마나 움직였나"* 라는 시장
        질문이다 — 주문이 어디로 나가는지와 무관하다 (그 질문은 `나갈 수 있나` 열이 맡는다).

        ⚠️ **15분봉 넷으로 잰다.** 1시간봉을 쓰면 마지막 봉이 방금 열렸을 때 창이
        몇 분짜리가 되어, 조용해 보이는 것이 진짜인지 창이 짧은 탓인지 갈리지 않는다.
    """
    end = datetime.now(UTC)
    try:
        rows = await quotes.get_candles(
            _instrument(symbol),
            Timeframe.M15,
            end - timedelta(minutes=RECENT_MINUTES),
            end,
        )
    except Exception as exc:
        _logger.warning(
            "recent_span_unreadable", payload={"symbol": symbol, "error": str(exc)[:120]}
        )
        return None
    if not rows:
        return None
    high = max(item.high for item in rows)
    low = min(item.low for item in rows)
    if low <= 0:
        return None
    return float((high - low) / low * 100)


def _exit_view(got: object) -> dict[str, Any]:
    """호가 판정을 화면이 읽을 모양으로 (2026-08-20).

    Args:
        got: `probe_book` 의 결과. 예외일 수도 있다 (`gather(return_exceptions=True)`).

    Returns:
        `{read, ok, why, bid_gap, ask_gap, bid_depth, ask_depth}`.

    Note:
        ⭐ **`read` 와 `ok` 를 나눠 낸다.** 못 읽은 것을 `ok: true` 로만 주면 화면이
        *"검사했고 괜찮다"* 로 그린다 — 실제로는 주문 거래소에 그 계약이 없어서 못 읽은
        것일 수 있다 (실측: SNDK_USDT · SKHY_USDT 는 라이브에만 있다).
    """
    if not isinstance(got, Liquidity):
        return {"read": False, "ok": True, "why": "호가를 못 읽었다"}
    return {
        "read": got.read,
        "ok": got.ok,
        "why": got.why,
        "bid_gap": float(got.bid_gap_pct),
        "ask_gap": float(got.ask_gap_pct),
        "bid_depth": float(got.bid_depth),
        "ask_depth": float(got.ask_depth),
    }


def _decimal(value: object) -> Decimal | None:
    """숫자로 읽어 본다 — 못 읽으면 None (0 으로 채우지 않는다)."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _float(value: object) -> float | None:
    """화면용 실수 — 못 읽으면 None."""
    found = _decimal(value)
    return None if found is None else float(found)
