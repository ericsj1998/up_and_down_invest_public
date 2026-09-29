"""거래소 **한 벌 스냅샷** — 화면이 판마다 묻던 것을 시장마다 한 번으로 (T330 · 2026-09-30).

## 왜

2026-09-30 17:48 KST 실계좌 CPU 가 버스트 기준선 위로 뛰었다. 원인은 매매가 아니라 **화면**이었다:
브라우저가 RUN 카드 40개를 10초마다 읽고(`/walkforward/live/<판>`), 카드 요청마다 API 가 Gate 에
`positions/<종목>` · 조건부 · 잔고를 **새로** 물었다. 콘솔 상태(`/exchange/state`)도 종목 41개 x 3
호출이었다. 10분에 거래소 호출 12,699(닫힌 화면 2,414의 5.3배) · api 컨테이너 80%.

## 규칙

- **표시 전용이다.** 러너의 판정 · 손절 · 대조 · 되살리기는 자기 조회를 그대로 쓴다.
  한 벌은 최대 `SNAPSHOT_TTL_S` 낡을 수 있고, 그 낡음이 매매 경로에 들어가면 안 된다
  (절대 규칙 #5 · `exchange()` 규약).
- **요청이 있을 때만 받는다.** 배경 루프가 없다 — 아무도 화면을 안 보면 호출 0 이다.
  같은 시장의 동시 요청은 `TtlCache` 키 락으로 합류한다(한 벌 받는 동안 나머지는 기다린다).
- **못 하는 어댑터는 None.** `BookSnapshotter` 가 아니면(바이낸스 · 주식 페이퍼) 부르는 쪽이 종목별
  경로로 간다 — 동작이 바뀌지 않는다.
- **실패는 기억하지 않는다**(`TtlCache` 규칙). 한 번 실패가 TTL 만큼 이어지지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from updown.common.cache import TtlCache
from updown.common.domain.market import Balance
from updown.orchestration.walkforward.live_runner import BookSnapshotter

SNAPSHOT_TTL_S = 10.0
"""한 벌의 기억 시간(초) — 화면 폴링(RUN 카드 10초)과 같아 사람이 느끼는 신선도는 그대로다."""

_CACHE = TtlCache[Any]("exchange.snapshot", SNAPSHOT_TTL_S)


@dataclass(frozen=True, slots=True)
class VenueSnapshot:
    """한 시장의 사실 한 벌.

    Attributes:
        market: 거래소 코드.
        at: 받은 시각(UTC).
        balance: 계좌 잔고.
        positions: 종목 → 포지션 칸(`position_snapshot` 과 같은 칸 · `size` 0 은 없다).
        orders: 종목 → 미결 주문 줄들(`open_orders` 와 같은 칸).
        stops: 종목 → 조건부 줄들(`open_stops` 와 같은 칸).
    """

    market: str
    at: datetime
    balance: Balance
    positions: dict[str, dict[str, str]]
    orders: dict[str, list[dict[str, str]]]
    stops: dict[str, list[dict[str, str]]]

    def position_of(self, symbol: str) -> dict[str, str]:
        """그 종목의 포지션 칸 — 없으면 빈 사전(`position_snapshot` 과 같은 뜻)."""
        return dict(self.positions.get(symbol, {}))

    def age_s(self, now: datetime | None = None) -> float:
        """몇 초 됐나 — 화면이 낡음을 적을 수 있게."""
        return max(0.0, ((now or datetime.now(UTC)) - self.at).total_seconds())


async def venue_snapshot(market: str, orders: object) -> VenueSnapshot | None:
    """시장의 한 벌 — TTL 안이면 저장분, 지나면 **한 번만** 새로 받는다.

    Args:
        market: 거래소 코드(`GATE`).
        orders: 주문 어댑터(게이트가 준 것).

    Returns:
        한 벌. 어댑터가 `BookSnapshotter` 가 아니면 None — 부르는 쪽이 종목별 경로로 간다.

    Raises:
        Exception: 거래소 조회 실패는 그대로 올린다(기억하지 않는다).
    """
    if not isinstance(orders, BookSnapshotter):
        return None
    book: BookSnapshotter = orders

    async def fetch() -> VenueSnapshot:
        balance = await book.get_balance()
        got = await book.book_snapshot()
        return VenueSnapshot(
            market=market,
            at=datetime.now(UTC),
            balance=balance,
            positions=dict(got.get("positions", {})),
            orders=dict(got.get("orders", {})),
            stops=dict(got.get("stops", {})),
        )

    return await _CACHE.get_or_fetch(f"snap:{market}", fetch)


def forget(market: str | None = None) -> int:
    """기억을 지운다 — 시험과, 방금 값을 바꾼 것을 아는 쪽(주문 · 취소 · 청산 뒤)이 쓴다.

    Args:
        market: 이 시장만. None 이면 전부.

    Returns:
        지운 항목 수.
    """
    return _CACHE.forget(None if market is None else f"snap:{market}")
