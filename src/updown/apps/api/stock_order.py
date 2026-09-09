"""주식 주문 창의 문 — 능력표를 차트 주문(`custom`)에 댄다 (T250 · 2026-09-09 · 순수).

코인 차트 주문은 예산 x 배율로 수량을 내고 숏도 낸다. 주식은 **정수 주 · 배율 없음 · 숏 없음 ·
장중만**이다. 그 차이를 여기서 한 번에 판정한다 — `live_custom` 이 판을 띄우기 **전에** 부르고,
거절은 400/409 로 나간다.

⛔ 시장 이름으로 가르지 않는다. `MarketCapabilities`(config/markets.yml)와 캘린더가 말한 것만
쓴다 (T237 결정 ②).
코인(배율 허용 시장)은 값을 하나도 안 바꾸고 지나간다 — 코인 경로의 시험이 그대로여야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from updown.common.domain.capabilities import Lot, MarketCapabilities
from updown.common.domain.instrument import Market
from updown.common.domain.session import MarketCalendar, Tradability

HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409


class StockOrderRejectedError(ValueError):
    """주식 주문 창의 거절 — 말과 HTTP 상태.

    Attributes:
        status: 400(값이 틀렸다) 또는 409(지금은 안 된다 — 장 마감).
    """

    def __init__(self, message: str, *, status: int = HTTP_BAD_REQUEST) -> None:
        """예외를 만든다.

        Args:
            message: 사람에게 보일 이유.
            status: HTTP 상태.
        """
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class MarketHoursNow:
    """지금 장 상태 — 캘린더에서.

    Attributes:
        state: 열림/닫힘/모름.
        why: 이유.
        next_open: 다음 개장 (UTC). 모르면 None.
    """

    state: Tradability
    why: str
    next_open: datetime | None


def hours_of(calendar: MarketCalendar, market: Market, now: datetime) -> MarketHoursNow:
    """캘린더가 말하는 지금 장 상태.

    Args:
        calendar: 마켓 캘린더.
        market: 시장.
        now: UTC aware 시각.

    Returns:
        상태 + 다음 개장.
    """
    state, why = calendar.tradability(market, now)
    opens, _ = calendar.next_events(market, now)
    return MarketHoursNow(state=state, why=why, next_open=opens)


@dataclass(frozen=True, slots=True)
class StockOrderTerms:
    """문을 지난 뒤의 값.

    Attributes:
        leverage: 판이 쓸 배율 (주식은 1).
        margin: 판 예산. 주수를 받았으면 `주수 x 진입가`, 아니면 None(요청의 예산 그대로).
        shares: 받은 주수. 없으면 None.
    """

    leverage: Decimal
    margin: Decimal | None
    shares: int | None


def stock_order_terms(
    caps: MarketCapabilities,
    *,
    leverage: Decimal,
    short: bool,
    shares: object,
    entry: Decimal,
    hours: MarketHoursNow | None,
) -> StockOrderTerms:
    """능력표로 계획을 거른다 — 코인은 그대로, 주식은 배율 1 · 숏 거절 · 정수 주 · 장중만.

    Args:
        caps: 시장 능력표.
        leverage: 요청 배율.
        short: 숏인가.
        shares: 요청 주수 (없으면 None). 정수 수량 시장에서만 뜻이 있다.
        entry: 진입가 — 주수를 예산으로 바꿀 때 쓴다.
        hours: 지금 장 상태. 24시간 장이면 None.

    Returns:
        판이 쓸 값.

    Raises:
        StockOrderRejectedError: 배율>1(배율 없는 시장) · 숏(숏 없는 시장) · 주수가 양의 정수가
            아님 · 장 마감(409).

    Note:
        **수량은 여기서 계약수로 만들지 않는다.** 예산을 `주수 x 진입가` 로 두면 사이징 경로
        (`contracts_for`)가 정수 주수를 그대로 낸다 — 두 번째 사이징 경로를 만들지 않는다.
        예약 주문은 없다 — 페이퍼 브로커에 그 능력이 없어 장 밖이면 409 로 "다음 개장" 을
        말한다 (T250 ③).
    """
    if caps.leverage_allowed:
        return StockOrderTerms(leverage=leverage, margin=None, shares=None)
    if leverage > 1:
        raise StockOrderRejectedError(
            f"{caps.market.value} 는 배율을 쓸 수 없다 — 요청 배율 {leverage} (1 로 둔다)"
        )
    if short and not caps.short_allowed:
        raise StockOrderRejectedError(f"{caps.market.value} 는 숏이 없다 — 매수만 낼 수 있다")
    count: int | None = None
    if shares is not None:
        try:
            asked = Decimal(str(shares))
        except InvalidOperation as exc:
            raise StockOrderRejectedError(f"주수를 못 읽었다: {shares!r}") from exc
        if caps.lot is Lot.INTEGER and asked != asked.to_integral_value():
            raise StockOrderRejectedError(f"{caps.market.value} 는 정수 주만 받는다 — 요청 {asked}")
        if asked <= 0:
            raise StockOrderRejectedError(f"주수는 1 이상이어야 한다 — 요청 {asked}")
        count = int(asked)
    if hours is not None and hours.state is not Tradability.OPEN:
        when = "" if hours.next_open is None else f" · 다음 개장 {hours.next_open.isoformat()}"
        raise StockOrderRejectedError(
            f"{caps.market.value} 장이 열려 있지 않다 — {hours.why}{when} (예약 주문 없음)",
            status=HTTP_CONFLICT,
        )
    margin = None if count is None else entry * count
    return StockOrderTerms(leverage=Decimal(1), margin=margin, shares=count)


__all__ = [
    "MarketHoursNow",
    "StockOrderRejectedError",
    "StockOrderTerms",
    "hours_of",
    "stock_order_terms",
]
