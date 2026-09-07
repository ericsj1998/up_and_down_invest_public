"""T49 — 배율은 입력이 아니라 **출력**이다: exposure = r / 손절거리, 상한, 청산 검증, 거울상.

사용자 2026-08-22: *"확실한 자리에 기계적으로 들어간다면 래버리지는 같은 리스크로 배율을
극대화할 수 있는 방법이야."* — 배율을 고정하면 거짓이 되고, r/s 로 도출하면 참이 된다.
"""

from decimal import Decimal

import pytest

from updown.decision.sizing import DEFAULT_LEVERAGE_CAP, MAINTENANCE_MARGIN, size_for
from updown.orchestration.walkforward.ledger import MAINTENANCE_MARGIN as LEDGER_MMR


def test_a_tighter_stop_means_a_higher_exposure_for_the_same_risk() -> None:
    """같은 r=1% 에 손절 0.35% 면 ≈2.9배, 0.15% 면 ≈6.7배 — 확실한 자리 = 높은 배율."""
    wide = size_for(risk_pct=Decimal("0.01"), entry=Decimal(1000), stop=Decimal("996.5"))
    tight = size_for(risk_pct=Decimal("0.01"), entry=Decimal(1000), stop=Decimal("998.5"))
    assert wide.stop_pct == Decimal("0.0035")
    assert abs(wide.exposure - Decimal("2.857")) < Decimal("0.01")
    assert abs(tight.exposure - Decimal("6.667")) < Decimal("0.01")
    assert tight.exposure > wide.exposure
    # 실제로 거는 리스크는 둘 다 r 이다.
    assert abs(wide.risk_taken - Decimal("0.01")) < Decimal("0.0001")
    assert abs(tight.risk_taken - Decimal("0.01")) < Decimal("0.0001")


def test_twenty_x_fixed_on_a_035_stop_is_seven_percent_risk_not_one() -> None:
    """20배 고정 · 손절 0.35% = 리스크 7% — '같은 리스크' 가 아니다 (T24 산수)."""
    fixed = Decimal(20) * Decimal("0.0035")
    assert fixed == Decimal("0.07")
    sized = size_for(risk_pct=Decimal("0.01"), entry=Decimal(1000), stop=Decimal("996.5"))
    assert sized.exposure < Decimal(20)


def test_the_cap_holds_and_marks_the_trade_as_capped() -> None:
    """손절이 아주 좁으면 r/s 가 상한을 넘는다 — 상한에서 자르고 `capped` 로 표시."""
    sized = size_for(risk_pct=Decimal("0.02"), entry=Decimal(1000), stop=Decimal("999.5"))
    assert sized.exposure == DEFAULT_LEVERAGE_CAP and sized.capped
    assert sized.risk_taken < Decimal("0.02"), "상한에 걸리면 r 보다 작은 리스크로 들어간다"


def test_a_stop_outside_liquidation_is_unsafe() -> None:
    """청산 거리 = 1/L - 유지증거금. 손절이 그 밖이면 손절은 장식이다 — 안 간다."""
    # 손절 4% 인데 r 이 커서 배율이 높으면(1/L 이 4% 근처) 청산이 먼저 온다.
    sized = size_for(
        risk_pct=Decimal("0.9"), entry=Decimal(1000), stop=Decimal(960), leverage_cap=Decimal(100)
    )
    assert sized.exposure > 1 and sized.liquidation_room is not None
    assert sized.liquidation_room < sized.stop_pct and sized.safe is False
    safe = size_for(risk_pct=Decimal("0.01"), entry=Decimal(1000), stop=Decimal(960))
    assert safe.safe and safe.exposure < 1, (
        "손절이 넓으면 자본 일부만 쓴다 — 배율 1 미만 = 부분 노출"
    )


def test_the_mirror_is_free() -> None:
    """🪞 숏(손절이 진입 위)도 같은 식 — |진입 - 손절| 이라 부호가 없다."""
    long = size_for(risk_pct=Decimal("0.01"), entry=Decimal(1000), stop=Decimal(996))
    short = size_for(risk_pct=Decimal("0.01"), entry=Decimal(1000), stop=Decimal(1004))
    assert long.exposure == short.exposure and long.safe == short.safe


@pytest.mark.parametrize(
    ("risk", "entry", "stop"),
    [(Decimal(0), Decimal(1000), Decimal(990)), (Decimal("0.01"), Decimal(1000), Decimal(1000))],
)
def test_no_denominator_raises(risk: Decimal, entry: Decimal, stop: Decimal) -> None:
    """r 이 0 이거나 손절이 진입과 같으면 산수가 없다 — 조용히 1배로 떨어뜨리지 않는다."""
    with pytest.raises(ValueError):
        size_for(risk_pct=risk, entry=entry, stop=stop)


def test_the_ledger_uses_the_same_maintenance_margin() -> None:
    """유지증거금률은 한 곳(decision)에만 있다 — 원장이 그것을 가져다 쓴다."""
    assert LEDGER_MMR == MAINTENANCE_MARGIN == Decimal("0.005")
