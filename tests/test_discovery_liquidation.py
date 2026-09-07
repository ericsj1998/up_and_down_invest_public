"""청산은 **점수가 아니라 문이다** (T151 · Stage 0).

계획서 §0-2: 하드 제약은 청산 회피와 파산 회피 둘뿐이고, 나머지는 목적함수 안에서
판단한다. 그러므로 이 시험이 지키는 것은 하나다 — **청산 나는 전략이 통과하지 않는다.**

## ⚠️ "안 났다" 와 "안 난다" 를 가른다

2026-08-30 실측: 1분봉 45일 최대 역행 **3.89%** · 20배 청산 거리 4.5%.
청산 0건이지만 여유가 0.6%p 뿐이었다 — 다음 구간에 조금만 더 나가면 계좌가 없다.

⇒ 실제로 닿았나(`hit`)와 여유가 충분한가(`safe`)를 **따로** 답해야 한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from updown.decision.sizing import liquidation_distance
from updown.orchestration.discovery.liquidation import SAFETY, judge, max_leverage


class TestTheGate:
    def test_touching_liquidation_fails(self) -> None:
        """⛔ 닿으면 손익과 무관하게 탈락이다."""
        got = judge(Decimal(5), Decimal(20))  # 20배 청산 4.5% < 역행 5%
        assert got.hit
        assert not got.safe
        assert "청산에 닿았다" in got.why()

    def test_the_measured_20x_case_fails_on_margin(self) -> None:
        """🔴 **오늘 실제로 돌던 설정이다.**

        20배 · 최대 역행 3.89% → 청산 4.5% 에 안 닿았지만 여유가 1.16배뿐이다.
        D-2 가 요구하는 2배에 못 미치므로 **탈락**이다.
        """
        got = judge(Decimal("3.89"), Decimal(20))
        assert not got.hit, "닿지는 않았다"
        assert not got.safe, "그래도 여유가 모자라 통과하면 안 된다"
        assert got.margin < SAFETY
        assert "여유 부족" in got.why()

    def test_enough_room_passes(self) -> None:
        """역행 3.89% 를 2배로 견디려면 청산이 7.78% 밖 — 12배면 7.83% 다."""
        got = judge(Decimal("3.89"), Decimal(12))
        assert got.safe, got.why()
        assert got.why() == ""

    def test_zero_adverse_is_infinite_room(self) -> None:
        """⚠️ 역행이 0 이면 나눌 수 없다 — 여유는 무한이고 통과다."""
        got = judge(Decimal(0), Decimal(20))
        assert got.safe
        assert got.margin == Decimal("Infinity")

    @pytest.mark.parametrize("bad", [Decimal(0), Decimal(-1)])
    def test_bad_leverage_raises(self, bad: Decimal) -> None:
        with pytest.raises(ValueError, match="배율"):
            judge(Decimal(1), bad)

    def test_negative_adverse_raises(self) -> None:
        """역행이 음수라는 것은 부호를 잘못 넣었다는 뜻이다 — 조용히 통과시키지 않는다."""
        with pytest.raises(ValueError, match="역행"):
            judge(Decimal(-1), Decimal(10))


class TestItReusesTheOneDefinition:
    """⚠️ 청산 거리를 두 곳에서 계산하면 언젠가 갈린다."""

    @pytest.mark.parametrize("lev", [Decimal(3), Decimal(6), Decimal(12), Decimal(20)])
    def test_distance_matches_decision_layer(self, lev: Decimal) -> None:
        got = judge(Decimal(1), lev)
        assert got.distance == liquidation_distance(lev) * 100

    def test_it_does_not_reimplement(self) -> None:
        """🔴 오늘 봉 사전을 일곱 벌 만든 사고를 겪었다 — 같은 실수를 미리 막는다."""
        from pathlib import Path

        source = Path("src/updown/orchestration/discovery/liquidation.py").read_text(
            encoding="utf-8"
        )
        assert "from updown.decision.sizing import liquidation_distance" in source
        assert "1 / leverage" not in source, "청산 거리를 여기서 다시 계산하고 있다"


class TestMaxLeverage:
    """⭐ 판정을 뒤집어 *"몇 배까지 되나"* 를 답한다 — 사이징이 배율을 결과로 뽑게."""

    def test_it_agrees_with_judge(self) -> None:
        """🔴 두 함수가 어긋나면 하나는 거짓말이다."""
        mae = Decimal("3.89")
        cap = max_leverage(mae)
        assert judge(mae, cap).safe, f"상한 {cap} 을 그대로 썼는데 탈락했다"
        # 그보다 한 단계 높이면 떨어져야 한다
        assert not judge(mae, cap + Decimal(1)).safe

    def test_bigger_adverse_means_lower_leverage(self) -> None:
        assert max_leverage(Decimal(1)) > max_leverage(Decimal(5))

    def test_zero_adverse_caps_at_exchange_limit(self) -> None:
        """⚠️ "제한 없음" 을 무한으로 내면 쓰는 쪽이 터진다 — 거래소 상한을 낸다."""
        assert max_leverage(Decimal(0)) == Decimal(125)

    def test_the_measured_case_says_12x(self) -> None:
        """실측 역행 3.89% 로는 **12배 근처**가 상한이다 — 20배가 왜 탈락인지의 근거."""
        cap = max_leverage(Decimal("3.89"))
        assert Decimal(11) < cap < Decimal(13), cap
