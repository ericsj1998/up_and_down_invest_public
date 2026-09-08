"""예산 합 vs 계좌 — 허용치 판정.

2026-09-06 실계좌 오탐: 0.0049 USDT 부족에 전 판의 새 진입이 막혔다.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from updown.decision.risk.policy import (
    RiskConfigError,
    RiskSettings,
    funding_shortfall,
    load_settings,
)


@pytest.fixture(scope="module")
def settings() -> RiskSettings:
    return load_settings()


class TestFundingShortfall:
    def test_config_carries_the_tolerance(self, settings: RiskSettings) -> None:
        assert settings.funding_shortfall_tolerance_pct == Decimal("0.02")

    def test_a_fee_sized_dip_does_not_block(self, settings: RiskSettings) -> None:
        # 실측: 예산 300.00 · 계좌 299.99510284 → 0.0049 부족 = "0.00 많다" 로 막혔다
        assert (
            funding_shortfall(settings, budgets=Decimal("300"), account=Decimal("299.99510284"))
            is None
        )

    def test_exactly_at_the_tolerance_passes_and_beyond_blocks(
        self, settings: RiskSettings
    ) -> None:
        assert (
            funding_shortfall(settings, budgets=Decimal("300"), account=Decimal("294")) is None
        )  # 6 = 2%
        short = funding_shortfall(settings, budgets=Decimal("300"), account=Decimal("293"))
        assert short == Decimal("7")

    def test_zero_tolerance_is_the_old_behaviour(self, settings: RiskSettings) -> None:
        strict = replace(settings, funding_shortfall_tolerance_pct=Decimal("0"))
        assert funding_shortfall(
            strict, budgets=Decimal("300"), account=Decimal("299.999")
        ) == Decimal("0.001")
        assert funding_shortfall(strict, budgets=Decimal("300"), account=Decimal("300")) is None

    def test_bad_tolerance_is_rejected_not_silently_clamped(self, settings: RiskSettings) -> None:
        for bad in (Decimal("-0.1"), Decimal("1"), Decimal("1.5")):
            with pytest.raises(RiskConfigError):
                funding_shortfall(
                    replace(settings, funding_shortfall_tolerance_pct=bad),
                    budgets=Decimal("300"),
                    account=Decimal("300"),
                )
