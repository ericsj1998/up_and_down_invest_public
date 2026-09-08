"""**말랐을 때 나가는 길** (사용자 요구 2026-08-20).

Note:
    사용자 지적: *"말랐을 때, 어떻게 대처해야 하는지도 있어야 하는 거 아닌가?"* 맞다 —
    배너만 띄우는 것은 불났다고 알리고 소화기를 안 주는 것이다.

    🔴 **그런데 값은 기계가 정할 수 없다.** 실측 (SPCX_USDT · 2026-08-20):

    ```
    그때 시장가로 던졌다면   -420
    138.50 에 걸어 두었더니   -74   (15시간 31분 뒤 체결)
    ```

    138.50 은 -74 였고 130 이었으면 -280 이었다. **얼마에 거느냐가 곧 손실 결정**이라,
    화면이 계산해 나란히 놓고 **사람이 누른다.**

    ⛔ 이 경로는 **돈이 나가는 경로**다. 포지션을 늘릴 수 있는 길이 한 줄도 없어야 한다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

import pytest

from updown.orchestration.liquidity import escape_plan

# 🔴 SPCX 실측 — 표시가는 멀쩡하고 매수만 22% 아래다.
SPEC = {"mark_price": "139.06", "quanto_multiplier": "0.01", "order_price_round": "0.01"}
DRY = {"bids": [{"p": "108", "s": "30"}], "asks": [{"p": "138", "s": "330000"}]}
FULL = {
    "bids": [{"p": "139.00", "s": "5000"}, {"p": "138.90", "s": "5000"}],
    "asks": [{"p": "139.10", "s": "5000"}, {"p": "139.20", "s": "5000"}],
}
LONG = {"size": "5642", "entry_price": "139.773218716767"}
SHORT = {"size": "-5642", "entry_price": "139.773218716767"}


class TestTheWallThatTrappedSPCX:
    """그때 무엇이 막았는지를 숫자로 재현한다."""

    def test_the_limit_price_is_the_gate_rule(self) -> None:
        """🔴 표시가 139.06 → 걸 수 있는 최저가 111.25. 유일한 매수자는 108 이었다."""
        plan = escape_plan("SPCX_USDT", SPEC, DRY, LONG)
        assert plan is not None
        assert plan.limit == pytest.approx(Decimal("111.25"), abs=Decimal("0.01"))

    def test_hitting_the_bid_was_impossible(self) -> None:
        """⛔ **3.25 차이로 영영 안 만났다** — 그것이 15시간의 정체다."""
        plan = escape_plan("SPCX_USDT", SPEC, DRY, LONG)
        assert plan is not None
        assert plan.touch == Decimal("108")
        assert not plan.touch_ok, "108 은 한계가 111.25 밖이다"
        assert not plan.market_ok

    def test_the_suggestion_is_the_front_of_the_queue(self) -> None:
        """⭐ 롱을 닫으려면 **파는 줄 맨 앞**에 서야 한다 — 최저 매도호가를 한 눈금 밑으로.

        SPCX 를 실제로 푼 자리가 그것이었다.
        """
        plan = escape_plan("SPCX_USDT", SPEC, DRY, LONG)
        assert plan is not None
        assert plan.suggested == Decimal("137.99")
        # 한계 안이라 거래소가 받는다.
        assert plan.suggested > plan.limit

    def test_it_says_what_that_costs(self) -> None:
        """🔴 **값이 곧 손실 결정**이라 사람이 보고 눌러야 한다."""
        plan = escape_plan("SPCX_USDT", SPEC, DRY, LONG)
        assert plan is not None
        # (137.99 - 139.773) x 5642 x 0.01 = -100.6
        assert plan.realized == pytest.approx(Decimal("-100.6"), abs=Decimal("0.5"))
        # ⚠️ 즉시 나갔다면 훨씬 나빴다 — 그래서 기다린 것이 옳았다.
        assert plan.touch_realized is not None
        assert plan.touch_realized < plan.realized


class TestTheTwoWallsAreDifferent:
    """🔴 시장가와 지정가의 벽이 다르다 — 하나로 세면 SPCX 를 놓친다."""

    def test_a_healthy_book_needs_no_escape(self) -> None:
        """⭐ 시장가로 나갈 수 있으면 화면이 이 창을 **안 그린다.**"""
        plan = escape_plan("SPCX_USDT", SPEC, FULL, LONG)
        assert plan is not None
        assert plan.market_ok
        assert plan.touch_ok

    def test_a_six_percent_gap_blocks_market_but_not_limit(self) -> None:
        """⚠️ 격차 6% — **시장가는 막히고 지정가는 된다.** 이때가 이 창이 필요한 자리다.

        하나로 세면 이 구간이 통째로 안 보인다.
        """
        book = {"bids": [{"p": "130.7", "s": "500"}], "asks": [{"p": "139.10", "s": "500"}]}
        plan = escape_plan("SPCX_USDT", SPEC, book, LONG)
        assert plan is not None
        assert not plan.market_ok, "슬립 5% 를 넘었다"
        assert plan.touch_ok, "20% 안이라 지정가로는 걸 수 있다"


class TestTheShortSideMirrors:
    """숏은 방향이 전부 뒤집힌다 — 부호를 한 곳에서만 다뤄야 한다."""

    def test_the_limit_is_above_the_mark(self) -> None:
        plan = escape_plan("SPCX_USDT", SPEC, DRY, SHORT)
        assert plan is not None
        assert not plan.long
        assert plan.limit == pytest.approx(Decimal("166.87"), abs=Decimal("0.01"))

    def test_it_buys_at_the_front_of_the_buy_queue(self) -> None:
        """⭐ 숏을 닫으려면 사야 한다 — **사는 줄 맨 앞**은 최고 매수호가 한 눈금 위다."""
        plan = escape_plan("SPCX_USDT", SPEC, DRY, SHORT)
        assert plan is not None
        assert plan.suggested == Decimal("108.01")

    def test_a_falling_price_is_a_gain_for_a_short(self) -> None:
        """🔴 부호는 **계약 수**가 든다 — 뺄셈을 따로 뒤집으면 언젠가 한쪽만 고친다."""
        plan = escape_plan("SPCX_USDT", SPEC, DRY, SHORT)
        assert plan is not None
        # 진입 139.77 · 청산 108.01 → 숏은 이익이다.
        assert plan.realized > 0


class TestItRefusesToInvent:
    """⛔ 값을 못 읽으면 지어내지 않는다."""

    @pytest.mark.parametrize(
        ("spec", "held"),
        [
            (SPEC, {"size": "0", "entry_price": "139"}),  # 포지션이 없다
            ({"mark_price": "0"}, LONG),  # 표시가를 못 읽었다
        ],
    )
    def test_it_returns_none(self, spec: dict[str, Any], held: dict[str, Any]) -> None:
        assert escape_plan("X", spec, DRY, held) is None

    def test_an_empty_book_falls_back_to_the_mark(self) -> None:
        """⚠️ 반대편 호가가 아예 없는 것 자체가 마른 시장이라는 뜻이다."""
        plan = escape_plan("X", SPEC, {"bids": [], "asks": []}, LONG)
        assert plan is not None
        assert plan.touch is None
        assert not plan.touch_ok
        assert not plan.market_ok


class TestTheOrderCanOnlyShrink:
    """🔴 **돈이 나가는 경로다** — 포지션을 늘릴 길이 한 줄도 없어야 한다."""

    @staticmethod
    def _source() -> str:
        from updown.apps.api import exchange

        return inspect.getsource(exchange.escape_place)

    def test_it_is_always_reduce_only(self) -> None:
        """⛔ `OrderKind.CLOSE` 가 어댑터에서 `reduce_only` 가 된다. 빼면 반대 포지션이 열린다."""
        source = self._source()
        assert "OrderKind.CLOSE" in source
        for name in ("OrderKind.ENTRY", "OrderKind.TAKE_PROFIT", "OrderKind.STOP_LOSS"):
            assert name not in source

    def test_the_kind_still_maps_to_reduce_only(self) -> None:
        """⚠️ 어댑터 쪽이 바뀌면 여기가 조용히 깨진다 — 그 매핑을 같이 잠근다."""
        from updown.execution.gate_paper import GatePaperAdapter

        source = inspect.getsource(GatePaperAdapter.submit_order)
        cut = source.index("reduce_only=")
        assert "close" in source[cut : cut + 120]

    def test_the_side_is_the_opposite_of_the_holding(self) -> None:
        """⭐ 방향을 우리가 고르지 않는다 — 거래소가 말한 보유를 뒤집는다."""
        source = self._source()
        assert "Side.SELL if plan.long else Side.BUY" in source

    def test_the_size_comes_from_the_exchange(self) -> None:
        """⛔ 수량을 지어내면 남거나 넘친다 — 넘치면 반대 포지션이다."""
        source = self._source()
        assert "Decimal(plan.size)" in source
        assert "position_snapshot" in source

    def test_the_server_rechecks_the_limit(self) -> None:
        """🔴 화면이 계산한 값을 그대로 믿지 않는다.

        화면이 낡았으면 거래소가 거절하고, 그 실패는 *"주문 0건"* 으로만 보인다 (규칙 #8).
        """
        source = self._source()
        assert "plan.limit" in source
        assert "400" in source

    def test_it_goes_through_the_gateway(self) -> None:
        """⛔ 어댑터를 직접 만들지 않는다 (절대 규칙 #0)."""
        source = self._source()
        assert "_orders_adapter(market)" in source

    def test_the_key_changes_every_press(self) -> None:
        """⚠️ 사람이 두 번 누르는 것은 정상이다 — 첫 번째가 실패했을 수 있다."""
        source = self._source()
        assert "int(time.time())" in source

    def test_reading_places_nothing(self) -> None:
        """⛔ 보는 것과 거는 것을 가른다 — GET 이 주문을 내면 새로고침이 주문이 된다."""
        from updown.apps.api import exchange

        source = inspect.getsource(exchange.escape_view)
        assert "submit_order" not in source
