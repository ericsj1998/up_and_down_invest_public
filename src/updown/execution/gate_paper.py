"""Gate.io **testnet 페이크머니** 주문 어댑터 (T13 · spec §4.19).

## 🔴 이 파일이 여는 문

Phase 0~1 동안 `OrderGateway` 는 **어떤 조건에서도** 어댑터를 반환하지 않았다
(plan D-12). 이 어댑터가 그 차단의 절반을 연다 — **페이퍼 경로만**.

실주문 경로(`RoutingTarget.LIVE`)는 **그대로 막혀 있다.** 두 예외를 따로 둔 이유가
그것이고, `tests/test_live_gate.py` 가 그 분리를 검증한다.

## 잠금 셋 — 물리적으로

1. **testnet 이 아닌 베이스로는 만들어지지 않는다.** 생성자가 거부한다. 라이브 URL 을
   넘기는 코드는 예외로 죽고, 그것이 "실수로 라이브에 주문" 을 막는 첫 층이다.
2. **`GATE_TESTNET_*` 만 읽는다.** 라이브 키 이름(`GATE_API_*`)은 아예 안 본다 —
   환경에 라이브 키가 있어도 이 어댑터는 그것을 쓸 수 없다.
3. **잔고에 페이크머니 표식을 남긴다** (`broker="gate-testnet"`). 원장·화면이 어느 돈인지
   구별할 수 있어야 한다 — 페이크 성적을 실적으로 적는 것이 가장 나쁜 사고다.

## 조회는 위임한다

캔들·호가·시세는 `GateAdapter`(공개, 키 없음)가 이미 한다. 여기서 다시 만들면 두 벌이
되고 한쪽만 고쳐진다. ⚠️ **조회는 라이브 데이터를 본다** — testnet 호가창은 라이브와
다르므로 거기서 판단하면 다른 시장을 분석하는 셈이다 (주문만 testnet 이다).
"""

import contextlib
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Currency, Instrument, Side, Timeframe
from updown.common.domain.market import Balance, MarketStatus, OrderBook, Quote
from updown.common.domain.order import OrderRequest, OrderResult, OrderStatus
from updown.common.logging.setup import get_logger
from updown.common.numeric import zero
from updown.marketdata.adapter import Capability
from updown.marketdata.gate.adapter import GateAdapter
from updown.marketdata.gate.mapping import to_contract
from updown.marketdata.gate.trade_client import (
    STOP_EXPIRATION_S,
    STOP_REFRESH_BEFORE_S,
    GateApiError,
    GateTradeClient,
    price_text,
)
from updown.marketdata.shared_read import forget as forget_shared
from updown.marketdata.shared_read import shared

BROKER_NAME: Final = "gate-testnet"
LIVE_BROKER_NAME: Final = "gate"
"""실계좌 브로커 이름 (`GateLiveAdapter`). 테스트넷과 **다른 이름**이어야 원장이 갈린다."""
"""잔고·원장에 남는 브로커 이름.

🔴 **페이크머니라는 것이 이름에 있다.** 그냥 `gate` 로 두면 나중에 라이브 성적과 섞이고,
섞인 뒤에는 어느 거래가 진짜였는지 되살릴 수 없다.
"""

_logger = get_logger("execution.gate_paper")

_STATUS: Final[dict[str, OrderStatus]] = {
    "open": OrderStatus.SUBMITTED,
    "finished": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
}
"""Gate 주문 상태 → 도메인 상태.

⚠️ **`finished` 는 "끝났다" 이고 "다 채워졌다" 가 아니다.** IOC 시장가가 일부만 체결되고
끝날 수 있으므로, 실제 판정은 `left`(남은 수량)로 한다 — 이 표는 기본값이다.
"""


POSITION_MISSING = "POSITION_NOT_FOUND"
"""한 번도 거래 안 한 계약의 400 응답 라벨 — **오류가 아니라 "없다" 다**.

🔴 이것을 예외로 두면 계좌 조회까지 같이 죽는다 (`LiveRunner.exchange`). 실제로 화면이
*"계좌 잔액을 모른다"* 고 말했는데 원인은 ETH 를 아직 안 산 것이었다 (2026-08-19).

⛔ 문자열로 가른다 — Gate 는 label 을 본문에 담고 상태코드는 400 하나뿐이라, 다른
400 과 구별할 방법이 이것뿐이다. 문구가 바뀌면 **예외가 다시 새어** 조회가 실패하고,
그 방향이 맞다 (조용히 "포지션 없음" 이 되는 것보다 낫다).
"""


def _stop_expiring_soon(item: dict[str, Any], *, now: float | None = None) -> bool:
    """이 조건부 손절의 만료가 `STOP_REFRESH_BEFORE_S`(3일) 안으로 다가왔나 (장투 · 갭2).

    Args:
        item: Gate `price_orders` 한 줄. `create_time`(초)로 만료를 잰다.
        now: 기준 시각(초). 테스트가 주입한다. None 이면 지금.

    Returns:
        만료가 임박했으면 True — 그러면 `stops_for` 가 미리 갈아 끼운다.

    Note:
        ⚠️ **`create_time` 을 못 읽으면 True 를 낸다** — 언제 걸렸는지 모르면 갱신하는
        쪽이 안전하다(만료 위험을 방치하지 않는다). 갱신은 취소+재생성이라 비용이 낮다.
    """
    import time

    created = 0.0
    with contextlib.suppress(TypeError, ValueError):
        created = float(item.get("create_time", 0) or 0)
    if created <= 0:
        return True  # 언제 걸렸는지 모른다 — 갱신하는 쪽이 안전하다
    remaining = created + STOP_EXPIRATION_S - (now if now is not None else time.time())
    return remaining <= STOP_REFRESH_BEFORE_S


class GateTestnetOnlyError(RuntimeError):
    """testnet 이 아닌 곳에 이 어댑터를 붙이려 했다.

    Note:
        🔴 **이것이 라이브 차단의 첫 층이다.** 라이브 주문은 별도 어댑터가 별도 게이트를
        통과해서 열려야 하며, 페이퍼 어댑터에 라이브 URL 을 넘겨 여는 우회로는 없다.
    """


class GateLiveOnlyError(RuntimeError):
    """라이브 어댑터에 testnet 클라이언트를 넣으려 했다 — 실적에 페이크머니가 섞이는 방향이다."""


class GatePaperAdapter:
    """Gate testnet 주문 + 라이브 조회.

    Note:
        ⛔ **직접 만들지 않는다.** 획득은 `OrderGateway` 가 독점한다 (절대 규칙 #0).
        AST 정적 검사가 강제한다.
    """

    def __init__(self, trade: GateTradeClient, quotes: GateAdapter) -> None:
        """어댑터를 만든다.

        Args:
            trade: **testnet** 서명 클라이언트.
            quotes: 공개 조회 어댑터 (라이브 데이터).

        Raises:
            GateTestnetOnlyError: `trade` 가 testnet 이 아닌 경우.

        Note:
            🔴 생성자에서 막는 이유는 **나중에 막으면 늦기** 때문이다. 주문 메서드에서
            검사하면 그 전에 잔고·레버리지가 라이브 계정에 닿는다.
        """
        self._check_client(trade)
        self._trade = trade
        self._quotes = quotes

    _broker: str = BROKER_NAME
    """잔고·원장·공유조회 키에 남는 브로커 이름 — 서브클래스가 바꾼다."""

    def _check_client(self, trade: GateTradeClient) -> None:
        """이 어댑터가 받을 수 있는 클라이언트인가 — 페이퍼는 testnet 만."""
        if not trade.is_testnet:
            raise GateTestnetOnlyError(
                "GatePaperAdapter 에 testnet 이 아닌 클라이언트가 들어왔다 — "
                "페이퍼 어댑터로 라이브에 주문하는 경로는 없다. 라이브는 GateLiveAdapter 가 "
                "OrderGateway.live_adapter 를 통과해서 열린다 (spec §12.4)"
            )

    @property
    def capabilities(self) -> frozenset[Capability]:
        """이 어댑터가 할 수 있는 것.

        Note:
            조회 능력은 `GateAdapter` 것을 그대로 쓴다 — 위임하므로 같아야 한다.
            여기에 주문 능력을 더한다.

            ✅ **`CONDITIONAL_ORDERS` 를 넣었다** (실측 2026-08-17 · testnet).
            `/futures/usdt/price_orders` 로 실제로 걸었다 — 등록·목록 확인·취소 전부
            200 이었다. 이제 상위가 "서버 다운 중에도 브로커측 손절이 돈다" 고 믿어도 된다.

            ⚠️ 다만 **만료가 있다** (기본 24시간). 포지션을 들고 있는 동안 다시 걸어야
            하고, 그 관리는 러너 몫이다 — 만료되면 포지션은 있는데 손절이 없는 상태가
            되고 그것은 조용하다.
        """
        return self._quotes.capabilities | {
            Capability.SPOT,
            Capability.CONDITIONAL_ORDERS,
        }

    @property
    def is_testnet(self) -> bool:
        """붙어 있는 클라이언트가 testnet 인가 — 러너 로그(`testnet_orders`)가 이것을 적는다."""
        return self._trade.is_testnet

    @property
    def broker_name(self) -> str:
        """잔고에 찍히는 브로커 이름 (`gate-testnet` | `gate`)."""
        return self._broker

    # ── 조회는 위임한다 (라이브 데이터) ──────────────────────────────

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """캔들 — 공개 조회(라이브 시장)에 위임한다.

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
        """시세 — 공개 조회(라이브)에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            라이브 시장의 현재 시세.
        """
        return await self._quotes.get_quote(instrument)

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        """호가 — 공개 조회에 위임한다 (**라이브 데이터**).

        Args:
            instrument: 종목.

        Returns:
            라이브 시장의 호가.

        Note:
            ⚠️ **주문이 나가는 곳의 호가가 아니다.** 비용 측정(`measure_costs`)은 라이브
            시장의 성질을 재려는 것이므로 이쪽이 맞다. 하지만 *"지금 이 계약에서 나올 수
            있나"* 는 다른 질문이고, 그것은 `book_here` 가 답한다 (2026-08-20 사고).
        """
        return await self._quotes.get_orderbook(instrument)

    async def book_here(self, instrument: Instrument, limit: int = 20) -> dict[str, Any]:
        """**주문이 나가는 곳**의 호가창 (2026-08-20 사고).

        Args:
            instrument: 대상 종목.
            limit: 단계 수.

        Returns:
            `{bids: [{p, s}], asks: [...]}` — 잔량 `s` 는 **계약 수**다.

        Note:
            🔴 `contract_spec` 과 같은 이유다. 명세를 라이브에서 읽고 주문을 testnet 에
            내면 값이 전부 무효인 것처럼, **호가도 그렇다.**

            실측 2026-08-20 · SPCX_USDT:

            ```
            라이브   매수 1호가가 표시가에서 0.01%   → "건강하다"
            testnet  매수 1호가가 표시가에서 22.3%   → 팔 곳이 없다 (증거금 420 이 묶였다)
            ```
        """
        return await self._trade.order_book(to_contract(instrument), limit=limit)

    async def stops_for(
        self, instrument: Instrument, trigger: Decimal, *, long: bool
    ) -> str | None:
        """브로커측 손절이 **그 가격으로** 걸려 있게 만든다 (없으면 걸고, 다르면 다시 건다).

        Args:
            instrument: 대상 종목.
            trigger: 원장이 정한 손절가.
            long: 보유가 롱인가.

        Returns:
            새로 건 조건부 주문 id. 이미 맞게 걸려 있었으면 None.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **거래소에 묻는다.** "걸었다" 를 기억하지 않는다 — Gate 조건부는 24시간에
            만료되고 그 소멸이 조용해서, 우리 기억은 만료를 모른다.

            ⭐ **가격이 다르면 갈아 끼운다.** 본절 상향처럼 원장이 손절을 옮기면 옛것을
            취소하고 새로 건다. 옛것을 남기면 두 가격에서 발동하려 하고, 하나는
            `reduce_only` 라 조용히 실패한다.

            ⛔ 손절을 **내리는** 방향은 여기서 막지 않는다 — 방향 검증은 원장·RiskManager
            몫이고(절대 규칙 #3), 집행이 값을 판단하면 SSoT 가 둘이 된다.
        """
        contract = to_contract(instrument)
        existing = await self._trade.list_stops(contract)
        # ⚠️ **같은 모양으로 비교한다.** 한쪽만 꼬리 0 이 붙어 있으면 매번 다르다고
        #    판단해 걸어 둔 손절을 취소하고 다시 건다 — 그 사이가 무방비다.
        wanted = price_text(trigger)
        keep = False
        stale: list[str] = []
        for item in existing:
            got: object = item.get("trigger")
            priced: object = (
                cast("dict[str, Any]", got).get("price") if isinstance(got, dict) else None
            )
            # 🔴 **만료가 임박하면 미리 갱신한다** (장투 · 갭2). 같은 가격이라도 30일
            #    만료가 3일 안으로 오면 갈아 끼워 만료를 연장한다 — 안 그러면 day 30 에
            #    손절이 조용히 만료돼 무방비 창이 생긴다.
            if priced is not None and str(priced) == wanted and not _stop_expiring_soon(item):
                keep = True
                continue
            fid: object = item.get("id")
            if fid is not None:
                stale.append(str(fid))
        if keep and not stale:
            return None  # 넉넉히 살아 있다 — 그대로 둔다
        # 🔴 **등록을 먼저, 취소를 나중에** (2026-09-03 사고). 예전 순서(취소→등록)는
        #    그 사이에 재시작(SIGTERM)이 꽂히면 손절 0개로 죽었고, 되살리기가 계획의
        #    뿌리를 잃어 포지션이 고아가 됐다. 뒤집으면 최악이 "잠깐 손절 2개" —
        #    하나가 발동하면 나머지는 빈 포지션에 reduce_only 라 무해하고, 30초 점검이
        #    잉여분을 거둔다. 무방비보다 이중이 낫다.
        made = None if keep else await self._trade.place_stop(contract, trigger, long=long)
        for fid_text in stale:
            await self._trade.cancel_stop(fid_text)
        return made

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태 — 무기한 선물은 24시간이다.

        Args:
            instrument: 종목.

        Returns:
            조회 어댑터가 답하는 상태. 인터페이스를 맞추기 위해 있다 — 주식 어댑터는 여기서
            휴장·조기마감을 답한다 (C2-4).
        """
        return await self._quotes.get_market_status(instrument)

    # ── 주문 (testnet 페이크머니) ────────────────────────────────────

    async def get_balance(self) -> Balance:
        """페이크머니 잔고.

        Returns:
            `broker="gate-testnet"` 인 잔고 — **페이크라는 것이 이름에 있다**.

        Note:
            `positions_value` 는 미실현 손익이 아니라 **포지션 평가액**이다. Gate 가
            `position_margin` 을 주므로 그것을 쓴다 — 미실현을 여기 넣으면 총자산이
            두 번 세어진다.

            ⭐ **판들이 나눠 쓴다** (2026-08-29 사고 · Binance 쪽에서 IP 밴까지 갔다).
            판 여럿이 **같은 계좌**를 각자 물어보고 있었다 — 같은 걸음에 겹친 것을
            하나로 합친다 (`shared_read` · 시한 2초).
        """
        account = await shared(f"{self._broker}:account", self._trade.get_account)
        total = Decimal(str(account.get("total", "0")))
        available = Decimal(str(account["available"]))
        # ⚠️ evolved-classic 모드는 `position_margin` 을 0 으로 준다 (2026-08-26 실측:
        #    total 10,134 · available 9,434 · position_margin "0" — 잠긴 증거금 699 가
        #    표시에서 증발했다). 잠긴 돈의 사실은 total-available 이다 — 필드가 살아
        #    있는 모드도 있으니 큰 쪽을 쓴다.
        margin_field = Decimal(str(account.get("position_margin", "0")))
        # ⭐ 대기 주문이 잡은 증거금(`order_margin`)은 포지션이 아니다 (2026-09-05) —
        #    total-available 에 섞여 "잡혀 있는 증거금 8.11 · 포지션 없음" 으로 보였다.
        #    따로 낸다 (`margins()`).
        order_margin = Decimal(str(account.get("order_margin", "0") or "0"))
        locked = max(margin_field, total - available - order_margin, Decimal(0))
        return Balance(
            broker=self._broker,
            currency=Currency.USD,
            cash=available,
            positions_value=locked,
            # ⚠️ USDT 를 원화로 환산하지 않는다. 환율을 여기서 끼우면 투자 손익과 환손익이
            #    섞이고, 그 분리는 Unified Portfolio 의 책임이다 (spec §4.18).
            fx_rate_snapshot=None,
            fetched_at=datetime.now(UTC),
        )

    async def margins(self) -> dict[str, str]:
        """계좌 요약의 돈 네 칸 — **어디에 얼마가 있나** (2026-09-05 · 콘솔 카드용).

        Returns:
            `{total, available, position_margin, order_margin}` 문자열. 거래소가 말한 값 그대로다.

        Note:
            🔴 `available` 만 보여 주면 옮긴 돈을 잃은 돈으로 읽는다 — 이 프로젝트가 반복한 실수다.
            총액과 대기 주문 증거금까지 나란히 두면 "8.11 이 어디 갔나" 가 화면에서 답이 된다.
        """
        account = await shared(f"{self._broker}:account", self._trade.get_account)
        return {
            "total": str(account.get("total", "0")),
            "available": str(account.get("available", "0")),
            "position_margin": str(account.get("position_margin", "0")),
            "order_margin": str(account.get("order_margin", "0") or "0"),
        }

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        """**거래소가 말하는** 포지션 — 원장이 아니다.

        Args:
            instrument: 볼 종목.

        Returns:
            비어 있으면 `{}`. 있으면 계약수·평단·표시가·미실현손익·레버리지·증거금·
            청산가를 **문자열로** 담는다.

        Raises:
            GateApiError: `POSITION_NOT_FOUND` 이외의 조회 실패. 전부 `{}` 로 바꾸면 있는
                포지션을 없다고 말하게 된다 — 손절도 안 걸고 이어받지도 않는다.

        Note:
            🔴 **원장과 계좌를 나란히 봐야 한다.** 원장은 손익률을 곱해 나가는 모형이고
            계좌는 사실이다. 둘이 갈리는 순간이 반드시 오는데(반올림·수수료·펀딩·
            부분체결), 계좌를 화면에 안 띄우면 그 차이를 **전략 성과로 오해한다**
            (§1-0s 관측 규약).

            ⚠️ 값을 `Decimal` 로 바꾸지 않고 문자열로 넘긴다 — 이 값들은 표시용이고,
            판정에 쓰이면 SSoT 가 둘이 된다 (절대 규칙 #4).
        """
        try:
            raw = await self._trade.get_position(to_contract(instrument))
        except GateApiError as exc:
            # 🔴 **한 번도 거래 안 한 계약은 400 `POSITION_NOT_FOUND` 다** (2026-08-19
            #    실측). 그것은 오류가 아니라 *"포지션이 없다"* 이고, 여기서 예외로 새면
            #    이 호출을 감싼 `exchange()` 전체가 실패해 **계좌 잔액까지 못 읽는다.**
            #
            #    사용자에게는 이렇게 보였다: `계좌 잔액 (지갑) — 모른다 · 거래소 조회가
            #    실패했다`. 실제로는 ETH 를 아직 안 샀을 뿐이었다.
            #
            # ⛔ **다른 오류는 삼키지 않는다.** 전부 `{}` 로 바꾸면 진짜 조회 실패가
            #    "포지션 없음" 이 되고, 그것은 **있는 포지션을 없다고 말하는 것**이라
            #    훨씬 나쁘다 — 손절도 안 걸고 이어받지도 않는다.
            if POSITION_MISSING in str(exc):
                return {}
            raise
        # ⚠️ Gate 는 **포지션을 닫아도 행을 지우지 않는다** — `size` 가 0 인 껍데기가 남는다.
        #    그것을 "보유 중" 으로 읽으면 화면이 없는 포지션의 청산가를 띄운다.
        if zero(raw.get("size")):
            return {}
        keep = (
            "size",
            "entry_price",
            "mark_price",
            "unrealised_pnl",
            "leverage",
            "margin",
            "liq_price",
            "value",
        )
        return {name: str(raw.get(name, "")) for name in keep}

    async def open_positions(self) -> list[dict[str, str]]:
        """계정에 **열려 있는 포지션 전부** — 종목을 몰라도 부를 수 있다.

        Returns:
            `symbol` 을 채운 행들. 없으면 빈 목록.

        Note:
            🔴 `position_snapshot` 과 짝이지만 **묻는 방식이 반대**다. 저쪽은 "이 종목의
            포지션" 이고 이쪽은 "무엇이 열려 있나" 다. 콘솔은 후자가 필요하다 —
            판이 지워져도 포지션은 남고, **그 고아를 찾는 것이 콘솔의 존재 이유**다
            (2026-08-25 GT ADA 원장-무소유 116계약).

            그동안 콘솔은 `orders.get_positions()` 를 불렀는데 그건 어댑터가 아니라
            **그 안의 TradeClient** 메서드였다. `AttributeError` 가 warning 으로
            삼켜져 *"거래소에 열린 포지션"* 축이 조용히 죽어 있었다 (2026-08-30 발견).

            ⚠️ `size` 0 인 껍데기는 뺀다 — Gate 는 포지션을 닫아도 행을 안 지운다.
        """
        rows = await self._trade.get_positions()
        out: list[dict[str, str]] = []
        for raw in rows:
            if zero(raw.get("size")):
                continue
            row = {name: str(value) for name, value in raw.items()}
            row["symbol"] = str(raw.get("contract") or raw.get("symbol") or "")
            out.append(row)
        return out

    async def account_book(self, limit: int = 30) -> list[dict[str, str]]:
        """자금 변동 원장 — **청산을 가르는 근거** (T14-2).

        Args:
            limit: 가져올 줄 수.

        Returns:
            `{type, change, time, text}` 목록. 못 읽으면 빈 목록.

        Note:
            ⛔ **여기서 해석하지 않는다.** 무엇이 청산인지는 부르는 쪽(`LiveRunner`)이
            정한다 — 어댑터가 판단하면 거래소마다 다른 규칙이 어댑터에 스며든다.

            ⚠️ 실패해도 빈 목록이다. 이 값은 **분류**에 쓰이지 리스크 감소 행동을 막는
            데 쓰이지 않는다 (§1.2.1).
        """
        rows = await self._trade.account_book(limit=limit)
        return [
            {
                "type": str(row.get("type", "")),
                "change": str(row.get("change", "")),
                "time": str(row.get("time", "")),
                "text": str(row.get("text", "")),
                "contract": str(row.get("contract", "")),
            }
            for row in rows
        ]

    async def set_leverage(self, instrument: Instrument, leverage: Decimal) -> None:
        """그 계약의 **격리 마진 배율**을 바꾼다 (사용자 요구 2026-08-19).

        Args:
            instrument: 대상 종목.
            leverage: 새 배율.

        Raises:
            GateApiError: 거래소가 거부한 경우.

        Note:
            🔴 **거래소와 원장이 같은 배율을 봐야 한다.** 원장만 바꾸면 우리가 계산한
            계약수와 거래소가 잡는 증거금이 어긋나고, 그 어긋남은
            `INSUFFICIENT_AVAILABLE` 로만 나타난다 (절대 규칙 #8).

            ⚠️ **포지션이 열려 있으면 거래소가 거부하거나 청산가를 즉시 옮긴다.** 그래서
            부르는 쪽이 보유 여부를 먼저 본다.
        """
        await self._trade.set_leverage(to_contract(instrument), leverage)

    async def position_closes(
        self, instrument: Instrument, limit: int = 30
    ) -> list[dict[str, str]]:
        """닫힌 포지션의 **실현 손익** — 주문 이력에는 없는 값이다 (2026-08-19).

        Args:
            instrument: 대상 종목.
            limit: 가져올 줄 수.

        Returns:
            `{time, pnl, side, text, max_size, long/short_price, pnl_fee, pnl_fund …}` 목록.
            못 읽으면 빈 목록.

        Note:
            ⚠️ **포지션 하나의 손익**이지 주문 하나의 손익이 아니다. 분할 청산이면 여러
            체결이 한 줄로 합쳐진다.
        """
        rows = await self._trade.position_closes(to_contract(instrument), limit=limit)
        return [
            {
                "time": str(row.get("time", "")),
                "pnl": str(row.get("pnl", "")),
                "side": str(row.get("side", "")),
                "text": str(row.get("text", "")),
                "max_size": str(row.get("max_size", "")),
                "first_open_time": str(row.get("first_open_time", "")),
                "long_price": str(row.get("long_price", "")),
                "short_price": str(row.get("short_price", "")),
                # ⭐ T236 — 실제 수수료·펀딩·누적 계약: 원장 비용 정렬(`_align_fee`)이 읽는다.
                #    빼먹으면 정렬이
                #    조용히 건너뛴다 (1.6.2~1.6.3 실측 · "청산 행에 pnl_fee 가 없다").
                "pnl_fee": str(row.get("pnl_fee", "")),
                "pnl_fund": str(row.get("pnl_fund", "")),
                "pnl_pnl": str(row.get("pnl_pnl", "")),
                "accum_size": str(row.get("accum_size", "")),
            }
            for row in rows
        ]

    async def account_margin(self) -> Decimal:
        """**계정 전체가 잡고 있는 증거금** — 모든 포지션의 합.

        Returns:
            합계. 포지션이 없으면 0.

        Note:
            🔴 **`available` 에서 빠진 돈은 잃은 돈이 아니다** (2026-08-19 사고). 포지션이
            열리면 Gate 가 필요한 증거금을 available 에서 떼어 붙이는데, 감사가 그것을
            *"원장이 사실과 갈렸다"* 로 읽었다 — 실측:

            ```
            원장 998.48 · available 700.95  → 29.8% 차이라고 경보
            그런데 잡힌 증거금 297.09 를 더하면 998.04 — 계정은 멀쩡했다
            ```

            ⛔ 이 프로젝트가 반복하는 실수다: **옮긴 것을 잃은 것으로 센다.**

            ⚠️ **계좌 요약의 `position_margin` 은 못 쓴다** (2026-08-18 실측: 포지션이
            margin=500 으로 열려 있는데 계좌 요약은 0 이었다). 격리 마진 포지션을 계좌
            수준에서 세지 않는 것으로 보인다 — 그래서 포지션을 직접 훑는다.
        """
        # ⭐ 종목별이 아니라 **계정 전체**를 묻는 조회다 — 판들이 나눠 쓴다.
        rows = await shared(f"{self._broker}:positions", self._trade.get_positions)
        total = Decimal(0)
        for row in rows:
            if zero(row.get("size")):
                continue
            with contextlib.suppress(ArithmeticError, ValueError):
                total += Decimal(str(row.get("margin", "0") or "0"))
        # 🔴 **대기 중인 우리 주문이 잡은 증거금도 우리 돈이다** (2026-09-05 실계좌 첫날).
        #    러너가 낸 진입 지정가 두 건(BTC·ETH · POC)이 `order_margin` 8.12 를 잡자 available 이
        #    그만큼 줄었고, 자가 점검이 "예산 합이 계좌보다 8.12 많다" 로 **전 판의 새 진입을
        #    막았다**. 옮긴 것을 잃은 것으로 센 것 — 위 포지션 증거금과 같은 실수의 다른 얼굴이다.
        with contextlib.suppress(Exception):
            account = await shared(f"{self._broker}:account", self._trade.get_account)
            total += Decimal(str(account.get("order_margin", "0") or "0"))
        return total

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """**주문이 나가는 곳**의 계약 명세 (2026-08-19 사고).

        Args:
            instrument: 대상 종목.

        Returns:
            계약 명세.

        Note:
            🔴 **조회 어댑터(라이브)가 아니라 주문 클라이언트(testnet)에 묻는다.** 명세를
            라이브에서 읽고 주문을 testnet 에 내면, 두 곳의 호가 단위가 다를 때 우리가
            만든 가격이 전부 무효가 된다 — 실제로 ETH 손절이 그렇게 거절됐다.

            ⚠️ 계약 명세는 수량·눈금·레버리지 한도를 정하는 값이다. **어느 거래소의
            값인지가 곧 그 값의 뜻**이다.
        """
        return await self._trade.contract(to_contract(instrument))

    async def identity(self) -> dict[str, str]:
        """이 어댑터가 붙은 계정의 식별자.

        Returns:
            `{user_id, tier, ip_whitelist, margin_mode, testnet}`.

        Note:
            🔴 **testnet 여부를 함께 낸다.** 화면이 그것을 보여 주지 않으면 페이크머니
            성적을 실적으로 읽는 사고가 가능하다 — 이 프로젝트에서 가장 나쁜 사고다.
        """
        detail = await self._trade.get_identity()
        account = await shared(f"{self._broker}:account", self._trade.get_account)
        white = cast("list[Any]", detail.get("ip_whitelist") or [])
        return {
            "user_id": str(detail.get("user_id", "")),
            "tier": str(detail.get("tier", "")),
            # ⚠️ 목록이라 문자열로 이어 붙인다 — 화면은 표시만 한다.
            "ip_whitelist": ", ".join(str(item) for item in white),
            "margin_mode": str(account.get("margin_mode_name", "")),
            # 서브클래스(GateLiveAdapter)는 실계좌다 — 하드코딩하면 실계좌 화면에
            # "페이크머니" 가 뜬다
            "testnet": "true" if self._trade.is_testnet else "false",
        }

    async def _tick(self, instrument: Instrument, price: Decimal | None) -> Decimal | None:
        """지정가를 **거래소 호가 단위**로 맞춘다.

        Args:
            instrument: 종목.
            price: 원장이 정한 가격. None 이면 시장가라 그대로 None 이다.

        Returns:
            호가 단위로 내린 가격.

        Note:
            🔴 **밤새 익절이 전부 거부됐다** (2026-08-18 실측):

                400 INVALID_PARAM_VALUE
                "Invalid request parameter `price` value: digit is greater than 12"

            원장은 Decimal 로 나눗셈을 하므로 `64831.24479157999294557786249` 같은 값이
            나온다. 진입은 **시장가**라 통과했고(가격을 안 보낸다), 익절 지정가에서만
            터졌다 — 그래서 **포지션은 열렸는데 익절이 안 걸린** 상태가 됐다.

            ⚠️ **진입만 성공하는 것이 가장 나쁜 조합이다.** 손절은 조건부라 걸렸지만
            익절이 없으니 포지션이 방치됐다.

            ⛔ 원장의 값을 바꾸지 않는다 — 여기서 **전송용으로만** 맞춘다. 원장이 브로커
            제약에 맞춰 휘면 다른 브로커를 붙일 때 또 휘어야 한다 (`gate_text` 와 같은
            사상이다).

            ⚠️ **내림이다** (`ROUND_DOWN`). 익절을 올리면 안 닿을 수 있고, 손절을 내리면
            절대 규칙 #3 위반이다 — 방향이 다른 두 다리를 한 함수로 처리하므로 **더
            보수적인 쪽**을 고른다. 호가 단위가 0.1 이라 차이는 최대 0.1 달러다.
        """
        if price is None:
            return None
        spec = await self._quotes.contract_spec(instrument)
        step = Decimal(str(spec.get("order_price_round", "0.1")))
        if step <= 0:
            return price
        return (price / step).quantize(Decimal(1), rounding=ROUND_DOWN) * step

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """미결 지정가 주문들 — **거래소가 아는 것**만.

        Args:
            instrument: 볼 종목.

        Returns:
            표시용 문자열 사전 목록.

        Note:
            🔴 원장의 "대기" 와 다르다. 원장은 계획을 적어 두는 곳이고 이것은 거래소에
            실제로 걸려 있는 주문이다 — 둘이 갈리는 것을 눈으로 봐야 유령을 잡는다.
        """
        rows = await self._trade.list_orders(to_contract(instrument), status="open")
        keep = ("id", "size", "left", "price", "text", "status", "is_reduce_only", "create_time")
        return [{name: str(row.get(name, "")) for name in keep} for row in rows]

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        """조건부(스탑) 주문들.

        Args:
            instrument: 볼 종목.

        Returns:
            표시용 사전 목록. 발동 가격과 **만료 시각**을 담는다.

        Note:
            🔴 **만료가 중요하다.** Gate 조건부는 24시간에 사라지고 그 소멸이 조용하다 —
            걸었다는 기억은 만료를 모른다. 화면에 만료를 띄워야 사람이 알아챈다.
        """
        rows = await self._trade.list_stops(to_contract(instrument))
        out: list[dict[str, str]] = []
        for row in rows:
            # ⚠️ 중첩 사전이라 좁혀 준다 — Gate 응답은 · 안에 값을 넣는다.
            rule = cast("dict[str, Any]", row.get("trigger") or {})
            order = cast("dict[str, Any]", row.get("initial") or {})
            out.append(
                {
                    "id": str(row.get("id", "")),
                    "trigger_price": str(rule.get("price", "")),
                    "expiration": str(rule.get("expiration", "")),
                    "size": str(order.get("size", "")),
                    "reduce_only": str(order.get("reduce_only", "")),
                    "text": str(order.get("text") or row.get("text") or ""),
                    "create_time": str(row.get("create_time", "")),
                }
            )
        return out

    async def recent_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """**끝난 주문들** — 체결됐거나 취소된 것.

        Args:
            instrument: 볼 종목.

        Returns:
            최근 것이 앞. 체결가·수량·남은 수량·우리 멱등키를 담는다.

        Note:
            🔴 **주문이 나갔는데 콘솔에서 안 보였다** (사용자 신고 2026-08-18).
            콘솔은 **미결** 주문만 보여 줬는데, 시장가 진입은 즉시 체결돼 미결에 남지
            않는다 — 나간 흔적이 화면 어디에도 없었다.

            ⚠️ 포지션만으로는 부족하다. 포지션은 **지금 상태**이고 이 목록은
            **무슨 일이 있었나**다. 거절된 익절은 포지션에도 미결에도 안 남는다.

            ⚠️ `left`(남은 수량)를 함께 낸다 — `status=finished` 는 *"끝났다"* 이지
            *"다 채워졌다"* 가 아니다. IOC 시장가는 일부만 체결되고 끝날 수 있다.
        """
        rows = await self._trade.list_orders(to_contract(instrument), status="finished")
        keep = (
            "id",
            "size",
            "left",
            "price",
            "fill_price",
            "text",
            "status",
            "finish_as",
            "is_reduce_only",
            "create_time",
            "finish_time",
        )
        return [{name: str(row.get(name, "")) for name in keep} for row in rows[:40]]

    async def cancel_stop(self, stop_id: str) -> None:
        """조건부 주문 하나를 거둔다.

        Args:
            stop_id: 거래소 조건부 주문 id.
        """
        await self._trade.cancel_stop(stop_id)

    async def close_position(self, instrument: Instrument, idempotency_key: str) -> OrderResult:
        """포지션을 **시장가로 전량** 닫는다.

        Args:
            instrument: 종목.
            idempotency_key: 멱등키 (절대 규칙 #6).

        Returns:
            주문 결과.

        Raises:
            GateApiError: 포지션이 없으면 거래소가 거부한다.

        Note:
            🔴 **`size=0` + `close=true`** 로 보낸다 — 수량을 우리가 계산하면 부분체결·
            펀딩으로 어긋난 만큼이 반대 포지션으로 열린다. 거래소가 세는 것이 정확하다.
        """
        placed = await self._trade.close_position(
            to_contract(instrument), idempotency_key=idempotency_key
        )
        # 🔴 **주문을 냈으면 나눠 쓰던 계좌 값을 버린다** (2026-08-29). 잔고·증거금이
        #    방금 바뀌었는데 2초짜리 옛 값을 다음 판이 받으면 **없는 돈으로 수량을
        #    산정**하고 그 주문은 거절된다.
        forget_shared(f"{self._broker}:")
        return self._to_result(placed, idempotency_key)

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """주문을 낸다 (testnet 페이크머니).

        Args:
            order: 주문 요청. `quantity` 는 **계약 수**로 해석한다.

        Returns:
            결과. `broker_order_id` 는 Gate 주문 id 다.

        Raises:
            GateAuthError: 401.
            GateApiError: 그 외.

        Note:
            🔴 **방향을 부호로 옮긴다.** Gate 는 `side` 필드가 없고 `size` 의 부호가
            방향이다 — 매도가 음수다. 틀리면 반대 포지션이 열리고 손절이 이익 방향에 놓인다.

            🔴 **재시도하지 않는다.** 실패하면 던진다. 재시도는 부르는 쪽이 `find_order`
            로 체결 여부를 먼저 확인하고 해야 한다 (절대 규칙 #6).

            ⚠️ `quantity` 를 **계약 수**로 본다. BTC 수량을 넘기면 1만분의 1 만 산다.
            상위가 BTC 로 계산했다면 `quanto_multiplier` 로 나눠서 넘겨야 한다 —
            이 경계에서 단위를 바꾸지 않는 이유는, 바꾸면 어느 쪽이 계약 수인지 호출부가
            헷갈리기 때문이다.
        """
        size = int(order.quantity)
        if order.side is Side.SELL:
            size = -size
        placed = await self._trade.place_order(
            to_contract(order.instrument),
            size,
            idempotency_key=order.idempotency_key,
            price=await self._tick(order.instrument, order.price),
            # 익절·손절·청산은 포지션을 줄이는 주문이다. 빼면 반대 포지션이 새로 열린다.
            reduce_only=order.order_kind.value in {"take_profit", "stop_loss", "close"},
            # ⭐ 메이커 보장 (T60 축④) — 요청이 선언한 대로만. 시장가에는 그냥 무시된다.
            post_only=order.post_only,
        )
        # 🔴 **주문을 냈으면 나눠 쓰던 계좌 값을 버린다** (2026-08-29). 잔고·증거금이
        #    방금 바뀌었는데 2초짜리 옛 값을 다음 판이 받으면 **없는 돈으로 수량을
        #    산정**하고 그 주문은 거절된다.
        forget_shared(f"{self._broker}:")
        return self._to_result(placed, order.idempotency_key)

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """주문을 취소한다.

        Args:
            broker_order_id: Gate 주문 id.

        Returns:
            취소 결과.

        Note:
            ⚠️ 이미 체결된 주문의 취소 실패는 **정상**이다. 성공으로 접으면 "취소했다" 고
            믿는 포지션이 살아 있게 된다 (절대 규칙 #8) — 그래서 예외를 삼키지 않는다.
        """
        cancelled = await self._trade.cancel_order(broker_order_id)
        return self._to_result(cancelled, str(cancelled.get("text", "")))

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """주문 상태 — **재시도 전에 부른다** (절대 규칙 #6).

        Args:
            broker_order_id: Gate 주문 id, 또는 `t-` 로 시작하는 멱등키.

        Returns:
            도메인 상태. 주문이 없으면 `PENDING` — **아직 안 들어갔다는 뜻**이다.

        Note:
            🔴 **없는 주문을 `FAILED` 로 접지 않는다.** "없다"는 재시도해도 되는 상태이고
            "실패했다"는 원인을 봐야 하는 상태다. 섞으면 안 들어간 주문을 실패로 적고
            포기하거나, 실패한 주문을 다시 내밀게 된다.
        """
        found = await self._trade.find_order(broker_order_id)
        if found is None:
            return OrderStatus.PENDING
        return self._status_of(found)

    def _status_of(self, payload: dict[str, Any]) -> OrderStatus:
        """Gate 주문 → 도메인 상태.

        Args:
            payload: Gate 주문 응답.

        Returns:
            도메인 상태.

        Note:
            🔴 **`finished` 를 곧 `FILLED` 로 보지 않는다.** IOC 시장가는 일부만 체결되고
            끝날 수 있다. `left`(남은 수량)가 0 이 아니면 부분 체결이고, 그 구분이 없으면
            원장이 채워지지 않은 수량을 보유로 적는다.
        """
        raw = str(payload.get("status", ""))
        base = _STATUS.get(raw, OrderStatus.SUBMITTED)
        if base is not OrderStatus.FILLED:
            return base
        left = abs(int(payload.get("left", 0)))
        size = abs(int(payload.get("size", 0)))
        if left == size and size > 0:
            # 하나도 못 채우고 끝났다 — IOC 가 상대 호가를 못 만난 경우다.
            return OrderStatus.EXPIRED
        if left > 0:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.FILLED

    def _to_result(self, payload: dict[str, Any], key: str) -> OrderResult:
        """Gate 주문 → `OrderResult`.

        Args:
            payload: Gate 주문 응답.
            key: 멱등키.

        Returns:
            결과.

        Note:
            체결 수량은 `size - left` 다 (둘 다 부호가 있으므로 절댓값으로 센다).
            평단은 `fill_price` 이며 미체결이면 None 이다 — **0 으로 채우지 않는다.**
            0 은 "공짜로 샀다"는 뜻이 되고 손익이 그만큼 부풀려진다.
        """
        size = abs(int(payload.get("size", 0)))
        left = abs(int(payload.get("left", 0)))
        price = payload.get("fill_price")
        average = Decimal(str(price)) if price is not None and str(price) not in {"", "0"} else None
        _logger.info(
            "gate_paper_order_result",
            payload={
                "broker_order_id": str(payload.get("id", "")),
                "status": str(payload.get("status", "")),
                "size": size,
                "left": left,
                "fill_price": str(price),
            },
        )
        return OrderResult(
            broker_order_id=str(payload.get("id", "")) or None,
            idempotency_key=key,
            status=self._status_of(payload),
            filled_quantity=Decimal(size - left),
            average_price=average,
            ts=datetime.now(UTC),
            reason=str(payload.get("text", "")) or None,
        )


class GateLiveAdapter(GatePaperAdapter):
    """Gate **실계좌** 주문 어댑터 — 로직은 페이퍼와 같고 잠금이 반대다 (T157 · 2026-09-04).

    테스트넷에서 검증한 코드가 곧 실계좌 코드다 — 사용자 지적이 맞았다 (*"API 키만 바꿔서
    돌리면 되는 거잖아"*). 다른 것은 셋뿐이다: base URL 이 라이브, 키 이름이 `GATE_API_*`,
    잔고 브로커 이름이 `gate`. 그 셋을 여기와 `OrderGateway.live_adapter` 가 맡는다.

    ⛔ **직접 만들지 않는다** (절대 규칙 #0). `execution/gateway.live_adapter` 만 만들 수
    있고, 그 함수는 `APP_ENV=live` + `LIVE_ORDERS=1` + 실키 셋이 다 있어야 돌려준다.
    """

    _broker = LIVE_BROKER_NAME

    def _check_client(self, trade: GateTradeClient) -> None:
        """라이브 클라이언트만 받는다 — testnet 을 넣으면 페이크 성적이 실적 원장에 섞인다."""
        if not trade.is_live:
            raise GateLiveOnlyError(
                "GateLiveAdapter 에 라이브가 아닌 클라이언트가 들어왔다 — 실계좌 어댑터는 "
                "LIVE_BASE_URL 클라이언트만 받는다. 페이퍼는 GatePaperAdapter 다"
            )
