"""투자자 관점 비율 — 전부 Decimal · 못 구하면 None (T243).

None 은 "없음" 이지 0 이 아니다. 적자 회사의 PER 은 없는 것이지 음수가 아니고, 자본잠식의
부채비율은 없는 것이지
음수가 아니다 — 그런 것은 깃발(`flags`)로 따로 든다. 분모가 0 이하인 비율은 전부 None 이다.
"""

from __future__ import annotations

from decimal import Decimal

from updown.common.numeric import fixed_context

_ONE = Decimal(1)
_HUNDRED = Decimal(100)


def safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """분모가 양수일 때만 나눈다.

    Args:
        numerator: 분자.
        denominator: 분모.

    Returns:
        몫. 어느 쪽이 없거나 분모가 0 이하면 None.
    """
    if numerator is None or denominator is None or denominator <= 0:
        return None
    with fixed_context():
        return numerator / denominator


def add(*parts: Decimal | None, require_first: bool = True) -> Decimal | None:
    """항목 합 — 첫 항목은 있어야 하고, 나머지 없는 항목은 0 으로 본다.

    Args:
        parts: 항목들.
        require_first: 첫 항목이 없으면 None 을 돌려줄지.

    Returns:
        합. "총부채 = 장기차입 + 단기차입 + CP" 처럼 **없을 수도 있는 항목**을 더할 때 쓴다 —
        장기차입도 없으면
        빚이 없는 것이 아니라 모르는 것이다.
    """
    if require_first and (not parts or parts[0] is None):
        return None
    return sum((p for p in parts if p is not None), Decimal(0))


def market_cap(price: Decimal | None, shares: Decimal | None) -> Decimal | None:
    """시가총액 = 가격 x 발행주식수.

    Args:
        price: 종가.
        shares: 발행주식수.

    Returns:
        시총. 어느 쪽이 없거나 주식수가 0 이하면 None.
    """
    if price is None or shares is None or shares <= 0:
        return None
    return price * shares


def enterprise_value(
    cap: Decimal | None, total_debt: Decimal | None, cash: Decimal | None
) -> Decimal | None:
    """EV = 시총 + 총차입 - 현금(및 단기투자).

    Args:
        cap: 시총.
        total_debt: 총차입. 모르면 EV 도 None.
        cash: 현금·단기투자. 모르면 0.

    Returns:
        EV. 시총이나 차입이 없으면 None.
    """
    if cap is None or total_debt is None:
        return None
    return cap + total_debt - (cash or Decimal(0))


def cagr(first: Decimal | None, last: Decimal | None, years: int) -> Decimal | None:
    """연복리 성장률. 시작·끝이 양수여야 한다.

    Args:
        first: 시작 값.
        last: 끝 값.
        years: 년 수.

    Returns:
        비율 (0.12 = 12%). 어느 쪽이 0 이하면 None — 적자에서 흑자로의 "성장률" 은 정의되지 않는다.
    """
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    with fixed_context():
        ratio = last / first
        return Decimal(str(float(ratio) ** (1.0 / years))) - _ONE


def change_ratio(before: Decimal | None, after: Decimal | None) -> Decimal | None:
    """(after / before) - 1 — 희석(주식수 추세)에 쓴다.

    Args:
        before: 전 값.
        after: 후 값.

    Returns:
        변화율. 전 값이 0 이하거나 없으면 None.
    """
    quotient = safe_div(after, before)
    return None if quotient is None else quotient - _ONE


def as_percent(ratio: Decimal | None) -> Decimal | None:
    """비율 → 퍼센트.

    Args:
        ratio: 비율.

    Returns:
        x100. None 은 그대로.
    """
    return None if ratio is None else ratio * _HUNDRED


__all__ = [
    "add",
    "as_percent",
    "cagr",
    "change_ratio",
    "enterprise_value",
    "market_cap",
    "safe_div",
]
