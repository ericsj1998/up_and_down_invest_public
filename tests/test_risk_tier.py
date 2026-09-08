"""위험 등급 — MDD · 청산률 · 수면 아래 (T231 · 사용자 2026-09-09)."""

from updown.orchestration.report.risk import (
    TIER_RULES,
    liquidation_rate_pct,
    max_drawdown_pct,
    risk_tier,
    underwater_pct,
)


class TestMetrics:
    def test_underwater_counts_points_below_the_running_peak(self) -> None:
        # 고점 10 → 9(-10% 아래) → 11(새 고점) → 11(같음 = 위) → 8(아래)
        assert underwater_pct([10, 9, 11, 11, 8]) == 50.0
        assert underwater_pct([1.0]) == 0.0
        assert underwater_pct([1, 2, 3]) == 0.0

    def test_tiny_dips_are_not_underwater(self) -> None:
        # 고점 100 바로 아래 99.5(-0.5%)는 안 센다 · 97(-3%)은 센다 — 문턱 2%
        assert underwater_pct([100, 99.5, 99.8, 97]) == round(1 / 3 * 100, 2)
        assert underwater_pct([100, 99.5, 97], min_dd_pct=0) == 100.0

    def test_max_drawdown_is_peak_to_trough(self) -> None:
        assert max_drawdown_pct([100, 80, 120, 60]) == 50.0
        assert max_drawdown_pct([100, 110, 120]) == 0.0

    def test_liquidation_rate(self) -> None:
        assert liquidation_rate_pct(2, 200) == 1.0
        assert liquidation_rate_pct(0, 0) == 0.0


class TestTier:
    def test_safe_requires_all_three(self) -> None:
        assert risk_tier(mdd_pct=10, liquidation_rate_pct=0, underwater_pct=30) == "safe"
        # 청산이 하나라도 있으면 안전이 아니다
        assert risk_tier(mdd_pct=10, liquidation_rate_pct=0.1, underwater_pct=30) == "balanced"

    def test_balanced_then_aggressive(self) -> None:
        assert risk_tier(mdd_pct=40, liquidation_rate_pct=0.3, underwater_pct=65) == "balanced"
        e1 = risk_tier(mdd_pct=50.08, liquidation_rate_pct=0.09, underwater_pct=80)
        assert e1 == "aggressive"
        assert risk_tier(mdd_pct=60, liquidation_rate_pct=0, underwater_pct=10) == "aggressive"

    def test_rules_are_ordered_safe_inside_balanced(self) -> None:
        safe, balanced = TIER_RULES["safe"], TIER_RULES["balanced"]
        assert safe.mdd_pct <= balanced.mdd_pct
        assert safe.liquidation_rate_pct <= balanced.liquidation_rate_pct
        assert safe.underwater_pct <= balanced.underwater_pct
