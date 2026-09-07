"""시세·잔고·장 상태 값 객체 (spec §4.2, §4.18, §7, §9).

`MarketStatus` 가 단순 bool 이 아닌 이유는 spec §7 때문이다. VI·사이드카·거래정지는
"거래 불가"라는 결과만 같을 뿐 **대응이 다르다** — VI 는 해제 후 재분석, 거래정지는
포지션 동결 + 긴급 알림이다. 사유를 잃으면 그 분기를 만들 수 없다.

`OrderBook` 이 여기 있는 이유는 **호가창은 시세이지 비용 정책이 아니기** 때문이다.
"이 호가창에서 N원을 시장가로 채우면 평균 얼마인가"(`walk`)는 거래소의 사실이고,
그 사실을 수수료·세금과 합쳐 손익으로 바꾸는 것은 `common/costs.py` 의 몫이다 (spec §12.7).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Currency, Instrument, Side
from updown.common.numeric import fixed_context


class MarketSession(StrEnum):
    """시장 운영 상태 (spec §4.2 "정규장/VI/사이드카/휴장", §7).

    Attributes:
        REGULAR: 정규장.
        PRE_OPEN: 장 개시 전 동시호가.
        POST_CLOSE: 장후 시간외 (KRX 시간외종가·단일가 / 미국 애프터마켓).
            🔴 `CLOSED` 와 **다르다.** 적재된 봉이 이 구간에도 있고, 그것을 `CLOSED`
            로 접으면 "봉이 있는데 장이 닫혀 있다"는 모순이 생겨 분류가 무의미해진다.
            갭 계산에서 이 구간을 빼는 것이 핵심이라 이름이 필요하다
            (`docs/rules/stock_session_notes.md`).
        CLOSED: 휴장·장 종료.
        ALWAYS_OPEN: 24시간 장 — 코인 (spec §7). 휴장 개념이 없다.
        VI: 변동성완화장치 발동.
        SIDECAR: 사이드카 발동.
        CIRCUIT_BREAKER: 서킷브레이커 발동 (거래소 차원, spec §4.6 의 자체 브레이커와 다름).
        HALTED: 거래정지·관리종목·정리매매 (spec §7 — 보유 시 **포지션 동결**).
    """

    REGULAR = "regular"
    PRE_OPEN = "pre_open"
    POST_CLOSE = "post_close"
    CLOSED = "closed"
    ALWAYS_OPEN = "always_open"
    VI = "vi"
    SIDECAR = "sidecar"
    CIRCUIT_BREAKER = "circuit_breaker"
    HALTED = "halted"


@dataclass(frozen=True, slots=True)
class MarketStatus:
    """종목의 현재 장 상태 (spec §4.2 `get_market_status`).

    Attributes:
        instrument: 대상 종목.
        session: 운영 상태.
        is_order_allowed: 신규 주문 가능 여부. `session` 에서 파생 가능해 보이지만,
            브로커별 예외(장전 예약주문 허용 등)가 있어 어댑터가 명시적으로 답한다.
        as_of: 조회 시각 (UTC).
        next_open: 다음 개장 시각 (UTC). 24시간 장이면 None.
        next_close: 다음 마감 시각 (UTC). 24시간 장이면 None.

    Note:
        개장 시각을 코드에 하드코딩하지 않는다 (절대 규칙 #7). 미국 서머타임 때문에
        `next_open`/`next_close` 는 마켓 캘린더에서 온 값이어야 한다 (spec §12.3).
    """

    instrument: Instrument
    session: MarketSession
    is_order_allowed: bool
    as_of: datetime
    next_open: datetime | None
    next_close: datetime | None


@dataclass(frozen=True, slots=True)
class Quote:
    """실시간 시세 스냅샷 (spec §4.2 `get_quote`).

    Attributes:
        instrument: 대상 종목.
        last_price: 최종 체결가.
        bid: 최우선 매수호가. 제공하지 않는 브로커는 None.
        ask: 최우선 매도호가. 제공하지 않는 브로커는 None.
        as_of: **조회 시각(UTC)**.

    Note:
        `as_of` 가 필수인 이유는 spec §4.18 의 "조용히 오래된 값을 최신처럼 보여주지
        않는다" 요구 때문이다. staleness 판정의 유일한 근거다.
    """

    instrument: Instrument
    last_price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    as_of: datetime


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """호가창 한 단계 — 매수/매도가 **같은 행에 붙어 온다**.

    Attributes:
        bid_price: 이 단계의 매수호가.
        bid_size: 매수 잔량 (수량 단위).
        ask_price: 이 단계의 매도호가.
        ask_size: 매도 잔량 (수량 단위).

    Note:
        매수·매도를 두 리스트로 나누지 않고 한 행에 묶는 것은 업비트 응답
        (`orderbook_units`)의 형태를 그대로 따른 것이다. 나눠 담으면 매핑에서
        인덱스를 재조립해야 하고, 그 재조립이 틀려도 조용히 그럴듯한 값이 나온다.

        **1단계 사이의 관계만 의미가 있다** — `levels[0]` 의 bid/ask 가 최우선 호가이며,
        그 아래 단계는 각자 독립적으로 멀어진다.
    """

    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal


@dataclass(frozen=True, slots=True)
class BookFill:
    """호가창을 훑어 채운 결과 (`OrderBook.walk`).

    Attributes:
        average_price: 체결 평단가.
        filled_notional: 실제로 채운 금액. `is_complete` 가 False 면 요청보다 작다.
        levels_consumed: 소진한 호가 단계 수.
        is_complete: 요청 금액을 전부 채웠는가.

    Note:
        `is_complete=False` 를 **조용히 무시하면 슬리피지가 낙관적으로 나온다** — 호가창
        30단계로 못 채운 주문은 실제로는 그 밖의 더 나쁜 가격에서 체결되는데, 30단계
        평단만 보면 그 꼬리가 사라진다 (절대 규칙 #8).
    """

    average_price: Decimal
    filled_notional: Decimal
    levels_consumed: int
    is_complete: bool


@dataclass(frozen=True, slots=True)
class OrderBook:
    """호가창 스냅샷 (spec §4.2 확장 — `OrderBookAdapter`).

    Attributes:
        instrument: 대상 종목.
        levels: 최우선 호가부터의 단계들. `levels[0]` 이 1호가다.
        as_of: **조회 시각(UTC)**. 브로커 시계가 아니라 우리가 잰 값이다 (spec §4.18).

    Note:
        `Quote` 와 별도 타입인 이유는 rate limit 이다. 업비트에서 `/orderbook` 은
        `/ticker` 와 **다른 그룹**이라 시세 조회마다 호가를 함께 받으면 예산이 두 배로
        든다 (docs/platform/upbit_api_notes.md §5). `Quote.bid`/`ask` 가 None 인 것은 누락이
        아니라 그 설계의 결과다.
    """

    instrument: Instrument
    levels: tuple[OrderBookLevel, ...]
    as_of: datetime

    def __post_init__(self) -> None:
        """빈 호가창과 naive 시각을 경계에서 막는다 (절대 규칙 #7, #8).

        Raises:
            ValueError: 단계가 하나도 없거나 `as_of` 가 naive 인 경우.

        Note:
            빈 호가창을 통과시키면 `best_ask` 가 IndexError 로 터지는 자리가 호출부
            어딘가로 밀린다. **호가가 비었다는 것 자체가 사건**이므로 여기서 이름을 붙여
            막는다.

            교차 호가(bid >= ask)는 여기서 막지 않는다 — 24시간 장에서 드물게 관측될 수
            있고, 장시간 샘플링을 예외로 죽이는 대신 `costs.sample_slippage` 가 표본
            단위로 거부하고 개수를 센다.
        """
        if not self.levels:
            raise ValueError(
                f"호가창이 비어 있다: {self.instrument.symbol} — "
                "빈 호가창을 통과시키면 실패 지점이 호출부로 밀린다 (spec §7)"
            )
        if self.as_of.tzinfo is None:
            raise ValueError(f"as_of 는 timezone-aware 여야 한다 (spec §12.3): {self.as_of!r}")

    @property
    def best_bid(self) -> Decimal:
        """최우선 매수호가."""
        return self.levels[0].bid_price

    @property
    def best_ask(self) -> Decimal:
        """최우선 매도호가."""
        return self.levels[0].ask_price

    @property
    def mid(self) -> Decimal:
        """중간가 `(bid + ask) / 2` — **비용 측정의 기준점**이다.

        Note:
            체결가를 최종 체결가(`trade_price`)와 비교하지 않는 이유가 §5-1 함정 1 이다.
            연속 체결은 매수·매도를 번갈아 때리므로 체결가 사이의 차이는 어떤 간격으로
            재도 스프레드만큼 나온다(bid-ask bounce). 그것을 슬리피지로 더하면
            **스프레드를 두 번 센다.** mid 를 기준으로 재면 그 이중 계산이 구조적으로
            불가능하다.
        """
        with fixed_context():
            return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> Decimal:
        """최우선 매도호가 - 최우선 매수호가."""
        return self.best_ask - self.best_bid

    def walk(self, notional: Decimal, side: Side) -> BookFill:
        """시장가로 `notional` 만큼 채울 때의 평균 체결가를 구한다.

        Args:
            notional: 채울 금액 (원화 등 호가 통화 기준).
            side: `BUY` 면 매도호가를, `SELL` 이면 매수호가를 소진한다.

        Returns:
            체결 결과. 호가창 깊이가 모자라면 `is_complete=False` 로 **채운 만큼만** 담는다.

        Raises:
            ValueError: `notional <= 0` 인 경우.

        Note:
            분할 진입(spec §4.6)의 예상 체결가 산정에도 같은 함수를 쓴다 — 비용 측정
            전용 계산을 따로 만들면 측정한 것과 집행되는 것이 달라진다.

            **부분 체결을 예외로 만들지 않는다.** 얼마나 못 채웠는지가 곧 그 종목에서
            우리 규모가 감당 가능한지에 대한 답이므로, 호출부가 그 수치를 봐야 한다.
        """
        if notional <= 0:
            raise ValueError(f"채울 금액은 0 보다 커야 한다: {notional}")

        with fixed_context():
            remaining = notional
            spent = Decimal(0)
            quantity = Decimal(0)
            consumed = 0
            only_price: Decimal | None = None
            single_price = True

            for level in self.levels:
                price = level.ask_price if side is Side.BUY else level.bid_price
                size = level.ask_size if side is Side.BUY else level.bid_size
                if price <= 0 or size <= 0:
                    continue
                consumed += 1
                if only_price is None:
                    only_price = price
                elif price != only_price:
                    single_price = False
                take = min(remaining, price * size)
                spent += take
                quantity += take / price
                remaining -= take
                if remaining <= 0:
                    break

            if quantity <= 0 or only_price is None:
                raise ValueError(
                    f"호가창에 유효한 잔량이 없다: {self.instrument.symbol} {side.value}"
                )
            return BookFill(
                # 한 가격에서만 채웠으면 **그 가격이 곧 평단**이다. `spent / quantity` 로
                # 되돌리면 나눗셈이 두 번(수량 산출 + 평단 산출) 일어나 1e-33 만큼 어긋나고,
                # 그 오차가 `costs.sample_slippage` 에서 깊이충격을 **-0.0000…1bp** 로
                # 만든다 — 1호가에서 전량 체결됐는데 깊이충격이 음수인 것은 말이 안 되고,
                # "편도 >= 스프레드/2" 불변식도 깨진다.
                average_price=only_price if single_price else spent / quantity,
                filled_notional=spent,
                levels_consumed=consumed,
                is_complete=remaining <= 0,
            )


@dataclass(frozen=True, slots=True)
class Balance:
    """계좌 잔고 스냅샷 (spec §4.2 `get_balance`, §9 `account_balances`).

    Attributes:
        broker: 브로커 식별자. **`'paper'` 를 값으로 수용한다** — 가상 원장을 실계좌와
            동일 스키마로 저장해야 Unified Portfolio·Trade Card·로깅이 코드 변경 없이
            동작한다 (spec §4.19).
        currency: 잔고 통화.
        cash: 예수금.
        positions_value: 보유 포지션 평가금액.
        fx_rate_snapshot: 환산 시점 환율. 원화 계좌는 None.
        fetched_at: 수집 시각 (UTC).

    Note:
        `fx_rate_snapshot` 을 스냅샷으로 남겨야 **투자 손익과 환율 변동 손익을 분리**할
        수 있다 (spec §4.18). 미분리 시 해외주식 성과가 왜곡된다.
    """

    broker: str
    currency: Currency
    cash: Decimal
    positions_value: Decimal
    fx_rate_snapshot: Decimal | None
    fetched_at: datetime
