"""브레이크가 **지금 걸려 있나** 를 화면에 한 덩어리로 (사용자 2026-10-09).

## 무엇을 막으려는 시험인가

1. 화면이 다리 문과 **다른 자**로 세는 것 — 문턱과 정확히 같은 낙폭은 안 걸린다(엄격 부등호 · 연구
   걸음과 같다).
2. 브레이크 제외 다리(`isolated`)가 "걸림" 으로 찍히는 것 — 그 다리는 브레이크를 안 받는다.
3. 브레이크 없는 다리(삼각 숏)가 문턱 값을 지어내는 것 — `at` 은 None 이어야 한다.
4. 계좌 낙폭(펀드 원장)과 매매법 낙폭(브레이크 원장)이 섞이는 것 — `source` 로 출처를 밝힌다.
"""

from __future__ import annotations

from decimal import Decimal

from updown.analysis.playbook.types import DrawdownBrake
from updown.orchestration.rebalancer.legs import FundLeg, brake_view

BRAKE = DrawdownBrake(at=Decimal("0.10"), scale=Decimal("0.25"))


def _leg(key: str, brake: DrawdownBrake | None, *, isolated: bool = False) -> FundLeg:
    return FundLeg(
        playbook=key,
        attribution=f"{key}@0.1.0",
        symbols=("BTC_USDT",),
        leverage=Decimal(4),
        exposure=Decimal(2),
        timeframe="1h",
        slots=6,
        drawdown_brake=brake,
        isolated=isolated,
    )


LEGS = (_leg("돌파", BRAKE), _leg("삼각", None), _leg("MACD롱", BRAKE, isolated=True))


class TestNotEngaged:
    def test_at_the_peak_nothing_is_engaged(self) -> None:
        got = brake_view(LEGS, Decimal(0), own_pnl=True, names={"돌파@0.1.0": "돌파 롱"})
        assert got["engaged"] is False
        assert got["scale"] == "1"
        assert got["source"] == "own_pnl"
        assert got["recover_pct"] is None
        by = {row["playbook"]: row for row in got["legs"]}
        assert by["돌파@0.1.0"]["name"] == "돌파 롱"
        assert by["돌파@0.1.0"]["at"] == "0.10"
        assert by["삼각@0.1.0"]["at"] is None  # 브레이크 없는 다리는 문턱을 지어내지 않는다
        assert by["삼각@0.1.0"]["engaged"] is False

    def test_exactly_at_the_threshold_is_not_engaged(self) -> None:
        """연구 걸음 `equity < peak * (1 - dd)` 와 같은 엄격 부등호 — 문과 화면이 같은 자를 쓴다."""
        got = brake_view(LEGS, Decimal("0.10"), own_pnl=True)
        assert got["engaged"] is False
        assert got["drawdown_pct"].startswith("10.0")


class TestEngaged:
    def test_past_the_threshold_the_smallest_scale_shows(self) -> None:
        got = brake_view(LEGS, Decimal("0.2436"), own_pnl=True)
        assert got["engaged"] is True
        assert got["scale"] == "0.25"
        # 고점 회복에 필요한 실현 수익률 = 1 / (1 - 0.2436) - 1 = 약 32.2%
        assert abs(Decimal(got["recover_pct"]) - Decimal("32.21")) < Decimal("0.01")
        by = {row["playbook"]: row for row in got["legs"]}
        assert by["돌파@0.1.0"]["engaged"] is True
        assert by["MACD롱@0.1.0"]["engaged"] is False  # 브레이크 제외 다리는 안 걸린다
        assert by["MACD롱@0.1.0"]["isolated"] is True

    def test_only_isolated_legs_cannot_engage_the_fund(self) -> None:
        got = brake_view((_leg("MACD롱", BRAKE, isolated=True),), Decimal("0.5"), own_pnl=True)
        assert got["engaged"] is False
        assert got["scale"] == "1"


class TestNoLegs:
    def test_fund_level_brake_is_read_when_there_are_no_legs(self) -> None:
        got = brake_view((), Decimal("0.3"), fund_brake=BRAKE, own_pnl=False)
        assert got["source"] == "fund"
        assert got["engaged"] is True
        assert got["legs"][0]["name"] == "펀드"

    def test_no_brake_anywhere(self) -> None:
        got = brake_view((), Decimal("0.3"), fund_brake=None, own_pnl=False)
        assert got["engaged"] is False
        assert got["legs"] == []
