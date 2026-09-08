"""박스권 매매 계획 — 스마트 박스 (playbooks.md 확정 3).

막아야 하는 실패:

1. 🔴 **손절을 레벨 하단에 두는 것** — 폭이 약 1xATR 이라 노이즈에 털린다
2. 🔴 **상단 레벨에 안전지대를 또 얹는 것** — 이미 저항 구간이라 익절선만 내려간다
3. 진입을 안전지대 바닥에 두는 것 — 탐지 119건 중 진입 3건이었다
4. 라운딩 없이 두는 것 — 같은 박스가 매번 다른 셋업이 된다
"""

from decimal import Decimal

from updown.analysis.structures.box_range import (
    DEFAULT_TICK,
    SAFETY_MULTIPLE,
    SMART_RATIO,
    BoxPlan,
    round_down,
)
from updown.analysis.structures.level_book import Level, Roles, plan_for
from updown.common.domain.structure import PriceRange


def level(low: str, high: str) -> Level:
    """테스트용 레벨 — 접점은 계획에 안 쓰이므로 최소만 채운다."""
    return Level(
        zone=PriceRange(low=Decimal(low), high=Decimal(high)),
        born_at=0,
        valid_from=0,
        support_at=(0,) * 2,
        resistance_at=(0,) * 2,
    )


def raw_plan(lower: tuple[str, str], upper: tuple[str, str]) -> BoxPlan | None:
    """라운딩을 끈 계획 — 기하만 검증한다.

    ⚠️ 테스트 가격이 100~200 이라 `DEFAULT_TICK`(500) 으로 내리면 전부 0 이 된다.
    라운딩 자체는 `TestTickRounding` 이 따로 검증한다.
    """
    roles = Roles(upper=level(*upper), lower=level(*lower))
    return plan_for(roles, tick=Decimal(0))


class TestSafetyZone:
    def test_entry_stays_inside_the_smart_support(self) -> None:
        """🔴 **진입은 하단 스마트 박스 안에서 받는다** (사용자 확정 2026-08-17).

        지지 띠 100~110 이면 하단 스마트는 100~106.5 다. 1차는 그 윗변, 2차는 아랫변.
        """
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        assert plan.first == Decimal(100) + Decimal(10) * SMART_RATIO
        assert plan.second == Decimal(100)

    def test_stop_is_below_the_smart_support(self) -> None:
        """🔴 완충은 **하단 스마트 박스 높이** 기준이다 — 띠 전체를 쓰면 손절이 넓어진다.

        그래서 사용자의 *"아래 지지에서 손절선은 더 좁아졌고"* 가 성립한다.
        """
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        assert plan.stop == Decimal(100) - Decimal(10) * SMART_RATIO
        assert plan.stop < plan.second

    def test_target_is_the_upper_smart_floor(self) -> None:
        """🔴 익절은 **상단 스마트 박스의 아랫변**이다 (사용자 확정 2026-08-17).

        ```
        저항 박스   190~200 (높이 10)
        상단 스마트 183.5~190  (아랫변 = 190 - 10 x 0.65)
        ```

        저항까지 안 가고 그 아래에서 판다 — 되밀리는 경우를 피한다.
        """
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        assert plan.target == Decimal(190) - Decimal(10) * SMART_RATIO

    def test_entry_is_inside_the_support_smart_box(self) -> None:
        """진입은 **하단 스마트 박스** 안이다 — 지지 띠 전체가 아니라 아래 65%."""
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        assert plan.second == Decimal(100)
        assert plan.first == Decimal(100) + Decimal(10) * SMART_RATIO
        assert plan.first < Decimal(110)

    def test_safety_scales_with_the_smart_box(self) -> None:
        """두께는 **하단 스마트 박스** 높이에 비례한다."""
        wide = raw_plan(("100", "140"), ("190", "200"))
        assert wide is not None
        assert wide.stop == Decimal(100) - Decimal(40) * SMART_RATIO * SAFETY_MULTIPLE


class TestTwoLegs:
    def test_entry_is_split(self) -> None:
        """1회 매수를 2회로 나눈다 — 레벨이 **폭을 가진 구간**이라는 사실을 반영한다."""
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        assert plan.first != plan.second

    def test_first_leg_is_higher(self) -> None:
        """⚠️ 순서는 가격이 아니라 시간 — 지지에서는 위에서 내려온다."""
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        assert plan.first > plan.second


class TestArithmetic:
    def test_risk_and_reward_are_from_the_average(self) -> None:
        """🔴 분할 진입이라도 리스크는 **계획 평단** 기준이다 (spec §4.6)."""
        plan = raw_plan(("100", "110"), ("190", "200"))
        assert plan is not None
        # 하단 스마트 100~106.5 · 상단 스마트 아랫변 183.5
        first = Decimal(100) + Decimal(10) * SMART_RATIO
        average = (first + Decimal(100)) / Decimal(2)
        stop = Decimal(100) - Decimal(10) * SMART_RATIO
        target = Decimal(190) - Decimal(10) * SMART_RATIO
        assert plan.average == average
        assert plan.risk == average - stop
        assert plan.reward == target - average


class TestImpossible:
    def test_zero_width_level_gives_no_plan(self) -> None:
        """⛔ 안전지대가 0 이면 스마트 박스가 아니다 — 계획을 지어내지 않는다."""
        assert raw_plan(("100", "100"), ("190", "200")) is None

    def test_inverted_box_gives_no_plan(self) -> None:
        """익절이 1차 진입가보다 아래면 성립하지 않는다."""
        assert raw_plan(("100", "110"), ("95", "100")) is None

    def test_missing_side_gives_no_plan(self) -> None:
        """⛔ 상단이나 하단이 없으면 박스권 매매가 아니다."""
        assert plan_for(Roles(upper=level("190", "200"))) is None
        assert plan_for(Roles(lower=level("100", "110"))) is None
        assert plan_for(Roles()) is None


class TestTickRounding:
    """🔴 라운딩이 없으면 같은 박스가 매번 다른 셋업이 된다.

    실측에서 관측 396건이 286개의 "서로 다른" 셋업으로 세어졌고, 값 차이가 소수점
    아래였다. 표본이 부풀려져 §12.9 표본 30건 규칙이 무의미해진다.
    """

    def _big(self, offset: str) -> Roles:
        """호가 단위보다 훨씬 큰 가격 — 실제 BTC 규모."""
        base = Decimal("126000000") + Decimal(offset)
        return Roles(
            upper=level(str(base + Decimal("3000000")), str(base + Decimal("3100000"))),
            lower=level(str(base), str(base + Decimal("400000"))),
        )

    def test_nearby_boxes_collapse_to_one_setup(self) -> None:
        """🔴 **한 호가 안에서** 흔들리는 박스들은 같은 셋업이 되어야 한다."""
        plans = [plan_for(self._big(o)) for o in ("0", "1.234", "97.6", "301")]
        assert all(p is not None for p in plans)
        prices = {(p.first, p.second, p.stop, p.target) for p in plans if p is not None}
        assert len(prices) == 1

    def test_crossing_a_tick_is_a_different_setup(self) -> None:
        """⭐ 호가 경계를 넘으면 다른 값이 나오는 것이 **맞다**.

        라운딩은 잡음을 접는 것이지 서로 다른 자리를 뭉개는 것이 아니다.
        """
        near = plan_for(self._big("0"))
        far = plan_for(self._big("-120.5"))
        assert near is not None
        assert far is not None
        assert near.first != far.first

    def test_every_price_is_on_the_tick(self) -> None:
        """주문이 실제로 들어갈 수 있는 값이어야 한다."""
        got = plan_for(self._big("1.234"))
        assert got is not None
        for price in (got.first, got.second, got.stop, got.target):
            assert price % DEFAULT_TICK == 0

    def test_rounding_is_always_down(self) -> None:
        """⭐ 전부 내림이라 전부 보수적이다 — 섞으면 어느 쪽은 낙관이 된다."""
        assert round_down(Decimal("1499"), DEFAULT_TICK) == Decimal(1000)
        assert round_down(Decimal("1501"), DEFAULT_TICK) == Decimal(1500)
