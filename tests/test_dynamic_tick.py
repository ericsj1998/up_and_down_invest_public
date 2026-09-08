"""호가 눈금은 **종목과 가격**이 함께 정한다 (T18 ①).

🔴 이 파일이 막는 실패는 2026-08-18 에 실제로 일어났다.

`DEFAULT_TICK = 500` 은 주석에 "(원)" 이라 적힌 **업비트 상수**인데 모든 시장이 함께
썼다. Gate BTC_USDT 64,300 에서 500 은 **가격의 0.78%** — 5,000배 굵다.

```
상단 스마트 띠 폭 중앙값 44.4  <  눈금 500
→ 띠 전체가 눈금 한 칸에 들어간다
→ 1차 익절(띠 아랫변)과 2차(띠 윗변)가 같은 값이 된다
→ plan_for 가 계획을 거부한다

최근 60봉 · 숏 방아쇠 21건 → 계획이 선 것 1건
```

⛔ **반올림 방향의 문제가 아니었다.** 올림으로 바꿔도 1건이다.

그리고 시장 하나에 눈금 하나를 두면 **그중 어느 종목에서는 반드시 틀린다** — 명세 틱이
종목마다 10,000배 다르다 (BTC 0.1 · DOGE 0.00001). 0.5 를 XRP 2.1 에 쓰면 24% 다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from updown.common.costs import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_TICK,
    TICK_RATIO,
    MarketCosts,
    SlippageSource,
    TickUnknownError,
    load_cost_table,
    resolve_tick,
    tick_for,
)
from updown.common.domain.instrument import Market

ROUND_TRIP = Decimal("0.00157")
"""업비트 실측 왕복 비용 — 눈금이 이보다 굵으면 계획 가격을 뭉갠다."""


def bare(market: Market, **over: object) -> MarketCosts:
    """선언이 최소인 비용 블록."""
    fields: dict[str, object] = {
        "market": market,
        "fee_pct": Decimal("0.0005"),
        "tax_pct_sell": Decimal(0),
        "slippage_pct_one_way": Decimal("0.0001"),
        "slippage_source": SlippageSource.ASSUMED,
        "measured_at": None,
        "source": "테스트",
    }
    fields.update(over)
    return MarketCosts(**fields)  # pyright: ignore[reportArgumentType]


class TestFormula:
    def test_btc_reproduces_the_approved_value(self) -> None:
        """🔴 **이것이 `TICK_RATIO` 의 정의다.** 사용자가 승인한 0.5 가 나와야 한다."""
        assert tick_for(Decimal(64300), Decimal("0.1")) == Decimal("0.5")

    @pytest.mark.parametrize(
        ("price", "spec", "expected"),
        [
            ("3000", "0.01", "0.03"),
            ("150", "0.01", "0.01"),
            ("2.1", "0.0001", "0.0001"),
            ("0.24", "0.00001", "0.00001"),
        ],
    )
    def test_small_coins_fall_back_to_the_spec_tick(
        self, price: str, spec: str, expected: str
    ) -> None:
        """⚠️ 명세 틱보다 잘게 못 쪼갠다 — 작은 종목은 명세 그대로가 답이다."""
        assert tick_for(Decimal(price), Decimal(spec)) == Decimal(expected)

    @pytest.mark.parametrize(
        ("price", "spec"),
        [("64300", "0.1"), ("3000", "0.01"), ("150", "0.01"), ("2.1", "0.0001")],
    )
    def test_every_tick_is_far_under_the_cost(self, price: str, spec: str) -> None:
        """🔴 **눈금이 비용에 근접하면 계획이 뭉개진다.** 두 자릿수 여유가 있어야 한다."""
        share = tick_for(Decimal(price), Decimal(spec)) / Decimal(price)
        assert share < ROUND_TRIP / 10

    def test_it_never_returns_zero(self) -> None:
        """⛔ 0 으로 라운딩하면 가격이 통째로 지워진다 — 내림이면 그렇게 된다."""
        assert tick_for(Decimal("0.00001"), Decimal("0.00001")) == Decimal("0.00001")

    def test_a_zero_spec_is_rejected(self) -> None:
        """눈금 없이 가격을 적을 수는 없다 — 조용히 통과시키지 않는다."""
        with pytest.raises(ValueError, match="0 이하"):
            tick_for(Decimal(100), Decimal(0))

    def test_the_ratio_is_a_frozen_constant(self) -> None:
        """⛔ 성과를 보고 조정하지 않는다 — 값이 바뀌면 이 테스트가 먼저 깨진다."""
        assert Decimal("0.0000075") == TICK_RATIO


class TestResolution:
    def test_declared_symbols_win(self) -> None:
        """① 종목별 명세가 있으면 가격에서 눈금을 만든다."""
        costs = bare(Market.GATE, spec_ticks={"BTC_USDT": Decimal("0.1")})
        assert resolve_tick(costs, "BTC_USDT", Decimal(64300), krw=False) == Decimal("0.5")

    def test_a_market_override_is_second(self) -> None:
        """② 종목별 값이 없으면 시장 고정값을 쓴다."""
        costs = bare(Market.GATE, price_tick=Decimal("0.5"))
        assert resolve_tick(costs, "ETH_USDT", Decimal(3000), krw=False) == Decimal("0.5")

    def test_krw_keeps_the_old_default(self) -> None:
        """③ 🔴 **업비트 백테스트 재현을 지킨다.** 500 은 원화 상수로서는 맞다."""
        costs = bare(Market.UPBIT)
        assert resolve_tick(costs, "KRW-BTC", Decimal(90_100_000), krw=True) == DEFAULT_TICK

    def test_an_undeclared_foreign_market_raises(self) -> None:
        """④ 🔴 **이것이 이 태스크의 요점이다.** 조용히 원화 상수로 떨어지지 않는다."""
        costs = bare(Market.GATE)
        with pytest.raises(TickUnknownError, match="호가 눈금을 모른다"):
            resolve_tick(costs, "PEPE_USDT", Decimal("0.00001"), krw=False)


class TestTheRealConfig:
    def test_gate_declares_the_symbols_we_run(self) -> None:
        """⛔ 선언 안 된 종목으로는 판을 못 띄운다 — 설정이 그 목록이다."""
        gate = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE)
        assert {"BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT"} <= set(gate.spec_ticks)

    def test_upbit_declares_nothing_so_history_is_reproducible(self) -> None:
        """🔴 업비트가 선언하면 **4년치 실측값이 통째로 움직인다.**"""
        upbit = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.UPBIT)
        assert not upbit.spec_ticks
        assert upbit.price_tick is None
