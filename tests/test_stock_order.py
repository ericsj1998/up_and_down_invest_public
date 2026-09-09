"""T250 — 주식 주문 창의 문: 코인은 그대로 · 주식은 배율 1 · 숏 거절 · 정수 주 · 장중만."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from updown.analysis.playbook.select import load_playbooks
from updown.apps.api.stock_order import (
    HTTP_CONFLICT,
    MarketHoursNow,
    StockOrderRejectedError,
    hours_of,
    stock_order_terms,
)
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import Market, MarketGroup
from updown.common.domain.session import Tradability, load_calendar
from updown.orchestration.walkforward.live_runner import MARGIN_HEADROOM

NASDAQ = capabilities_of(Market.NASDAQ)
GATE = capabilities_of(Market.GATE)
OPEN = MarketHoursNow(Tradability.OPEN, "정규장", None)
CLOSED = MarketHoursNow(Tradability.CLOSED, "장전", datetime(2026, 9, 9, 13, 30, tzinfo=UTC))


class TestTerms:
    def test_coin_passes_untouched(self) -> None:
        got = stock_order_terms(
            GATE, leverage=Decimal(6), short=True, shares=None, entry=Decimal(100), hours=None
        )
        assert got.leverage == Decimal(6) and got.margin is None and got.shares is None

    def test_stock_forces_leverage_one_and_refuses_short(self) -> None:
        with pytest.raises(StockOrderRejectedError, match="배율"):
            stock_order_terms(
                NASDAQ, leverage=Decimal(2), short=False, shares=1, entry=Decimal(100), hours=OPEN
            )
        with pytest.raises(StockOrderRejectedError, match="숏"):
            stock_order_terms(
                NASDAQ, leverage=Decimal(1), short=True, shares=1, entry=Decimal(100), hours=OPEN
            )

    def test_shares_become_budget_and_must_be_positive_integers(self) -> None:
        got = stock_order_terms(
            NASDAQ, leverage=Decimal(1), short=False, shares=3, entry=Decimal("316.22"), hours=OPEN
        )
        assert got.leverage == Decimal(1) and got.shares == 3
        assert got.margin == Decimal("958.25"), "948.66 / 0.99 를 센트 올림 — 러너 여유를 되돌린다"
        assert got.margin is not None and int(got.margin * MARGIN_HEADROOM / Decimal("316.22")) == 3
        with pytest.raises(StockOrderRejectedError, match="정수"):
            stock_order_terms(
                NASDAQ,
                leverage=Decimal(1),
                short=False,
                shares="1.5",
                entry=Decimal(100),
                hours=OPEN,
            )
        with pytest.raises(StockOrderRejectedError, match="1 이상"):
            stock_order_terms(
                NASDAQ, leverage=Decimal(1), short=False, shares=0, entry=Decimal(100), hours=OPEN
            )

    def test_closed_market_is_409_with_next_open(self) -> None:
        with pytest.raises(StockOrderRejectedError) as caught:
            stock_order_terms(
                NASDAQ, leverage=Decimal(1), short=False, shares=1, entry=Decimal(100), hours=CLOSED
            )
        assert caught.value.status == HTTP_CONFLICT and "13:30" in str(caught.value)

    def test_hours_of_reads_the_calendar(self) -> None:
        got = hours_of(load_calendar(), Market.NASDAQ, datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
        assert got.state is Tradability.CLOSED and got.next_open is not None


class TestWiring:
    def test_custom_playbook_now_covers_stock_groups(self) -> None:
        found = {item.playbook_id: item for item in load_playbooks()}["custom"]
        assert (
            MarketGroup.FOREIGN_STOCK in found.market_groups
            and MarketGroup.COIN in found.market_groups
        )

    def test_live_custom_passes_the_request_and_gates_before_starting(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent / "src/updown/apps/api/walkforward.py"
        ).read_text(encoding="utf-8")
        start = source.index('@router.post("/live/custom")')
        end = source.index('@router.post("/buy/{key}")')
        endpoint = source[start:end]
        assert "request: Request" in endpoint, "T242 관문은 request 가 있어야 본다"
        assert "request=request" in endpoint
        assert endpoint.index("stock_order_terms(") < endpoint.index("_live_start("), (
            "문은 판을 띄우기 전에"
        )

    def test_chart_frame_is_the_judge_frame_only_for_setupless_boards(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent / "src/updown/apps/api/walkforward.py"
        ).read_text(encoding="utf-8")
        assert 'if not book.setups and payload.get("timeframe")' in source
        assert source.count('payload.get("timeframe")') == 1, "셋업 있는 판의 축은 밖에서 못 바꾼다"
