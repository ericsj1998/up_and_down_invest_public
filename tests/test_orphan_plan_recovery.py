"""고아 스탑 복구의 대조 가드 — 진입가·방향 일치 (2026-09-01).

거래소 스탑이 사라진 고아를 인수하려면 RiskManager 가 DB 에 영속한 손절을 되읽어
다시 걸어야 한다 (`plan_by_symbol`). 그 계획을 **엉뚱한 포지션에 붙이지 않도록**
`_plan_fits_position` 이 진입가·방향을 대조한다 — 이 시험이 그 가드를 잰다.

막아야 하는 실패:
1. 🔴 종목만 같고 진입가가 다른 옛 매매의 손절을 지금 포지션에 붙인다.
2. 🔴 롱 포지션에 손절이 진입 위(=숏 손절)인 계획을 붙여 이익 방향에 손절이 선다.
"""

from __future__ import annotations

from decimal import Decimal

from updown.apps.api.walkforward import (
    _plan_fits_position,  # pyright: ignore[reportPrivateUsage]
)


def _plan(*, stop: str, entry: str) -> dict[str, str]:
    return {"trade_id": "t1", "direction": "롱", "stop": stop, "entry": entry}


def test_matching_long_plan_fits() -> None:
    # 롱: 손절(95)이 진입(100) 아래 · 진입가 일치.
    assert _plan_fits_position(_plan(stop="95", entry="100"), Decimal("100"), long=True) == ""


def test_matching_short_plan_fits() -> None:
    # 숏: 손절(105)이 진입(100) 위 · 진입가 일치.
    assert _plan_fits_position(_plan(stop="105", entry="100"), Decimal("100"), long=False) == ""


def test_entry_mismatch_is_rejected() -> None:
    # 진입가가 0.5% 넘게 어긋난다 — 다른 매매다.
    bad = _plan_fits_position(_plan(stop="95", entry="100"), Decimal("120"), long=True)
    assert bad and "진입가 불일치" in bad


def test_tiny_entry_drift_within_tolerance_fits() -> None:
    # 0.3% 차이는 허용 (호가·펀딩 오차).
    assert _plan_fits_position(_plan(stop="95", entry="100"), Decimal("100.3"), long=True) == ""


def test_wrong_direction_long_is_rejected() -> None:
    # 롱인데 손절(105)이 진입(100) 위 = 숏 손절 — 이익 방향에 손절이 선다.
    bad = _plan_fits_position(_plan(stop="105", entry="100"), Decimal("100"), long=True)
    assert bad and "방향" in bad


def test_wrong_direction_short_is_rejected() -> None:
    bad = _plan_fits_position(_plan(stop="95", entry="100"), Decimal("100"), long=False)
    assert bad and "방향" in bad


def test_missing_entry_is_rejected() -> None:
    bad = _plan_fits_position({"stop": "95"}, Decimal("100"), long=True)
    assert bad != "", "진입가를 못 읽으면 걸지 않는다"
