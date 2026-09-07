"""차트 주문이 고를 수 있는 **축** — 거래소가 정한다 (2026-08-30).

사용자 요구: *"'차트 주문'에서 10초봉, 1분봉 도 추가해줘."*

## ⚠️ 그런데 **모든 거래소가 다 주지는 않는다**

실측:

    GATE     10s · 1m · 5m · 15m · 1h · 4h · 1d
    BINANCE        1m · 5m · 15m · 1h · 4h · 1d      ← 10s 가 없다 (T62 P2b)

⇒ 목록을 화면에 박으면 Binance 에서 10초를 눌렀을 때 아무 일도 안 일어나거나
빈 화면이 나온다. **어느 축이 있는지는 거래소가 아는 사실**이므로 거래소에게 묻고
(`supported_frames` · T63 ②) 그 결과를 응답에 실어 화면이 단추를 그리게 한다.

⛔ 없는 축을 조용히 다른 축으로 바꾸지 않는다 — 그러면 화면의 축 이름과 그림이
갈리고, 사람은 자기가 고른 축을 보고 있다고 믿는다 (절대 규칙 #8).

## 🔴 하위 축은 **보기 전용**이다

10초·1분을 더한 것이 *"거기서 진입하라"* 는 뜻이 아니다. 5m 단독 진입을 폐기한 이유는
표본이 아니라 **비용**이고(필요 승률 100.7~100.9%), 그 산수는 축이 내려갈수록 나빠진다.
계획을 세우면 `required_win_rate` 가 그 값을 그대로 보여 준다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from updown.apps.api.analysis import OFFERED
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.provider import MarketDataProvider


def served(market: Market) -> tuple[Timeframe, ...]:
    """이 거래소가 주는 축들 — **계약으로** 묻는다 (T63 §2b).

    Args:
        market: 거래소.

    Returns:
        `OFFERED` 중 이 거래소가 주는 것들.

    Note:
        ⚠️ 구체 클래스를 나열하지 않는다. 새 거래소는 `QuoteAdapter` 를 지키는 순간
        이 시험에 자동으로 들어온다 — 목록을 손으로 늘리면 언젠가 빠뜨린다.
    """
    adapter = MarketDataProvider().adapter_for(market)
    assert isinstance(adapter, QuoteAdapter), f"{market.value} 가 조회 계약을 안 지킨다"
    return tuple(adapter.supported_frames(OFFERED))


class TestWhatIsOffered:
    def test_it_offers_ten_seconds_and_one_minute(self) -> None:
        """사용자가 요구한 둘이 목록에 있다."""
        assert Timeframe.S10 in OFFERED
        assert Timeframe.M1 in OFFERED

    def test_it_keeps_the_frames_that_were_there(self) -> None:
        """⚠️ 더하면서 **빼면 안 된다** — 쓰던 축이 사라지면 그것이 회귀다."""
        for frame in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
            assert frame in OFFERED, frame

    def test_it_is_ordered_fast_to_slow(self) -> None:
        """단추가 뒤죽박죽이면 사람이 매번 찾아야 한다."""
        assert list(OFFERED) == sorted(OFFERED, key=lambda item: list(Timeframe).index(item))


class TestTheExchangeDecides:
    """🔴 화면이 외우지 않는다 — 거래소마다 다른 사실이다."""

    def test_binance_does_not_serve_ten_seconds(self) -> None:
        """T62 P2b — 이것이 이 파일이 존재하는 이유다."""
        assert Timeframe.S10 not in served(Market.BINANCE)

    def test_binance_still_serves_one_minute(self) -> None:
        """⚠️ 10초가 없다고 1분까지 없는 것은 아니다 — 둘을 같이 빼면 안 된다."""
        assert Timeframe.M1 in served(Market.BINANCE)

    def test_gate_serves_both(self) -> None:
        got = served(Market.GATE)
        assert Timeframe.S10 in got
        assert Timeframe.M1 in got

    @pytest.mark.parametrize("market", [Market.BINANCE, Market.GATE])
    def test_it_never_invents_a_frame(self, market: Market) -> None:
        """⛔ 거래소가 우리가 안 물은 축을 돌려주면 화면에 없는 단추가 생긴다."""
        assert set(served(market)) <= set(OFFERED)


class TestItRefusesLoudly:
    """⛔ 없는 축을 조용히 갈아 끼우지 않는다 (절대 규칙 #8)."""

    SOURCE = Path("src/updown/apps/api/analysis.py").read_text(encoding="utf-8")

    def test_it_raises_instead_of_falling_back(self) -> None:
        assert "if only not in served:" in self.SOURCE
        spot = self.SOURCE.index("if only not in served:")
        after = self.SOURCE[spot : spot + 600]
        assert "HTTPException" in after, "조용히 다른 축으로 떨어지고 있다"

    def test_the_message_names_what_is_available(self) -> None:
        """⚠️ *"안 된다"* 만 말하면 무엇을 눌러야 하는지 모른다."""
        spot = self.SOURCE.index("if only not in served:")
        assert "고를 수 있는 축" in self.SOURCE[spot : spot + 600]

    def test_the_response_carries_the_served_frames(self) -> None:
        """화면이 단추를 이것으로 그린다 — 없으면 목록이 다시 화면 것이 된다."""
        assert '"frames": [item.value for item in served]' in self.SOURCE
