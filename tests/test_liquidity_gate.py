"""들어가기 전에 **나올 수 있는지** 본다 (2026-08-20 사고).

Note:
    🔴 SPCX_USDT 에 증거금 420 이 묶였다. 화면의 모든 숫자가 멀쩡했다 — 가격·손익·
    청산가가 전부 **표시가**(지수 기반) 기준이었기 때문이다. 정작 체결은 호가창에서
    나는데 그쪽은 이랬다:

    ```
    표시가      139.06
    최고 매수   108      ← 팔려면 여기를 때려야 한다 (22% 구멍)
    최저 매도   138      ← 사기는 쉬웠다
    ```

    ⚠️ **한쪽만 두꺼운 시장**이라 들어가긴 쉽고 나오긴 불가능했다. 실측 깊이:
    매수 **0** · 매도 5,763,163 — 총합만 재면 아주 건강해 보인다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from updown.apps.api import exchange as api
from updown.common.costs import CostConfigError, _liquidity  # pyright: ignore[reportPrivateUsage]
from updown.common.domain.instrument import Market
from updown.orchestration import liquidity


class TestConfig:
    """문턱은 **설정에서** 온다 — 코드에 박지 않는다 (개발 규약 1)."""

    def test_gate_declares_both_thresholds(self) -> None:
        """⭐ 5% 는 자의적이지 않다 — Gate 시장가 슬립 방어가 정확히 그 값이다."""
        from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table

        found = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE).liquidity
        assert found["max_gap_pct"] == Decimal("5.0")
        assert found["depth_multiple"] == Decimal("1.0")

    def test_a_market_without_the_block_is_untouched(self) -> None:
        """⛔ 선언이 없으면 검사를 안 한다 — 기존 판정값이 안 움직여야 한다 (§5.6.2)."""
        from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table

        assert not load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.UPBIT).liquidity

    def test_a_typo_is_refused(self) -> None:
        """🔴 오타를 조용히 넘기면 **켠 줄 알고 안 켜진** 상태가 된다 (규칙 #8)."""
        with pytest.raises(CostConfigError, match="모르는 칸"):
            _liquidity(Market.GATE, {"max_gap": 5})

    @pytest.mark.parametrize("value", [0, -1])
    def test_zero_or_negative_is_refused(self, value: int) -> None:
        """⛔ 검사를 끄려면 블록을 지운다 — 0 으로 표현하지 않는다."""
        with pytest.raises(CostConfigError, match="0 이하"):
            _liquidity(Market.GATE, {"max_gap_pct": value})


class TestItReadsTheOrderVenue:
    """🔴 **어느 거래소의 값인지가 곧 그 값의 뜻이다.**"""

    def test_it_asks_the_order_adapter_not_the_quotes(self) -> None:
        """조회는 라이브, 주문은 testnet 이다.

        실측 2026-08-20 · SPCX_USDT:

        ```
        라이브   매수 1호가가 표시가에서  0.01%   → "건강하다"
        testnet  매수 1호가가 표시가에서 22.34%   → 팔 곳이 없다
        ```

        라이브를 보면 증거금 420 이 묶인 **바로 그 계약**을 통과시킨다.
        """
        # ⭐ 계산은 orchestration/liquidity.py 로 내려갔다 (2026-08-20) — 러너가 들고
        #   있는 동안에도 **같은 검사**를 돌려야 하는데, 러너는 apps/ 를 못 쓴다.
        wrapper = inspect.getsource(api.liquidity_of)
        assert "_orders_adapter(market)" in wrapper, "조회 어댑터를 쓰면 라이브 호가를 본다"
        source = inspect.getsource(liquidity.probe_book)
        assert "book_here" in source
        # ⛔ 조회 어댑터의 호가창을 쓰면 안 된다.
        assert "get_orderbook" not in source

    def test_the_paper_adapter_has_its_own_book(self) -> None:
        """⚠️ `get_orderbook` 은 라이브에 위임한다 — 비용 측정용이라 그쪽이 맞다."""
        from updown.execution.gate_paper import GatePaperAdapter

        assert hasattr(GatePaperAdapter, "book_here")
        source = inspect.getsource(GatePaperAdapter.book_here)
        assert "_trade" in source, "주문 클라이언트에 안 묻는다"


class TestBothSidesSeparately:
    def test_depth_is_not_summed_across_sides(self) -> None:
        """🔴 총합을 재면 SPCX 는 매도 576만이라 건강해 보인다 — 매수는 **0** 이었다."""
        source = inspect.getsource(liquidity.read_book)
        assert "bid_depth" in source
        assert "ask_depth" in source
        # 두 값이 각각 문턱과 비교돼야 한다.
        assert "bid_depth < need" in source
        assert "ask_depth < need" in source

    def test_an_empty_side_is_refused(self) -> None:
        """⛔ 한쪽이 비었으면 그 자체가 사건이다 — 통과시키지 않는다."""
        source = inspect.getsource(liquidity.read_book)
        assert "호가 한쪽이 비었다" in source

    def test_unreadable_does_not_block(self) -> None:
        """⚠️ 조회 실패로 판을 못 띄우는 것도 사고다 (§1.2.1 과 같은 방향)."""
        source = inspect.getsource(liquidity.probe_book)
        cut = source.index("liquidity_unreadable")
        after = source[cut : cut + 400]
        assert "_blind(symbol)" in after, "못 읽었는데 막는다"


class TestTheGateIsWired:
    def test_the_run_door_checks_it(self) -> None:
        """🔴 돈이 나가는 자리에서 막아야 한다 — 목록 표시로는 아무것도 안 막힌다."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf._live_start)  # pyright: ignore[reportPrivateUsage]
        assert "liquidity_of" in source
        assert "409" in source

    def test_it_is_measured_against_notional_not_margin(self) -> None:
        """⚠️ 호가창이 받아야 하는 것은 **명목**이다 — 증거금이 아니라 배율을 곱한 값.

        ⭐ 배율은 예산 문(2026-08-20)과 **같은 값**을 쓴다 — 한 자리에서 풀어 둘이 나눠
        쓰지 않으면, 언젠가 한쪽만 배율을 안 곱한 채 남는다.
        """
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf._live_start)  # pyright: ignore[reportPrivateUsage]
        assert 'lever = Decimal(str(payload.get("leverage", 1)))' in source
        # 거래소도 넘긴다 (2026-09-06) — 없으면 바이낸스 판을 Gate 호가창으로 판정한다.
        assert "liquidity_of(symbol, margin * lever, market=market.value)" in source

    def test_the_tick_check_does_not_cover_this(self) -> None:
        """⛔ `_TRADABLE` 은 **설정 질문**이다 — 시장이 살아 있는지는 안 본다."""
        source = inspect.getsource(api)
        cut = source.index("_TRADABLE = ")
        assert "spec_ticks" in source[cut : cut + 200]
