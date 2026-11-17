"""방금 주문을 보낸 (거래소, 종목) — **조회가 아직 그 주문을 못 따라온 때**를 가른다 (T852 F2).

계좌 대조는 거래소 사실(포지션)과 원장을 서로 다른 순간에 읽는다. 그 사이에 우리 주문이
날아가고 있으면 둘은 잠깐 갈린다 — 그것은 "아무도 관리 안 하는 포지션" 이 아니라 **반영 중**이다
(실측 2026-11-17 00:00:16 HYPE — 시간 청산 체결 0.17 초 뒤 대조가 고아로 읽어 그 종목을 2 분
막았다). 주문이 나가는 자리는 어댑터의 `submit_order` 하나라 거기서만 적는다.

- 프로세스 안의 작은 표 하나다(종목 수만큼 · 수십 칸). 저장하지 않는다 — 재시작하면 날아가는
  주문도 없다.
- 시각은 monotonic 이다 — 벽시계가 뒤로 가도 "방금" 이 길어지지 않는다.
- ⛔ 판정이 아니다. 누가 이 표를 보고 **무엇을 할지**는 읽는 쪽이 정한다.
"""

from __future__ import annotations

import time

_SENT: dict[tuple[str, str], float] = {}


def mark_order(market: str, symbol: str) -> None:
    """이 종목에 주문을 보냈다(또는 방금 응답을 받았다)고 적는다.

    Args:
        market: 거래소 이름(`Market.value`).
        symbol: 종목.
    """
    _SENT[(market, symbol)] = time.monotonic()


def ordered_within(market: str, seconds: float) -> frozenset[str]:
    """`seconds` 안에 주문이 나간 종목들.

    Args:
        market: 거래소 이름.
        seconds: "방금" 의 길이(초).

    Returns:
        종목 집합.
    """
    now = time.monotonic()
    return frozenset(
        symbol for (venue, symbol), at in _SENT.items() if venue == market and now - at < seconds
    )


def forget_marks() -> None:
    """표를 비운다 — 시험용."""
    _SENT.clear()
