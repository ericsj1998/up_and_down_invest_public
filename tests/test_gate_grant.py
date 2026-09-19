"""펀드 문이 **허용 크기**를 돌려준다 — 줄여서 진입 · 낙폭 브레이크 (T286 · 2026-09-19).

이 둘은 4.47년 측정에서 잔고를 21,136 → 46,726 USDT 로 만든 장치이고, 실계좌에 붙는다.
연구 걸음(`scripts/research/scenarios/t279_leaderboard_bn.walk`)과 **같은 순서·같은 부등호**인지를
여기서 못 박는다 — 어긋나면 측정한 것과 다른 매매법이 실계좌에서 돈다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from updown.decision.portfolio_rules import drawdown_scale, notional_room
from updown.orchestration.rebalancer.gate import SlotGate

AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


class FakePort:
    """세션 한 개인 척 — 열린 수·노출·청산만 말한다."""

    def __init__(self, open_count: int = 0, exposure: Decimal = Decimal(0)) -> None:
        self._open = open_count
        self._exposure = exposure
        self.closed: list[tuple[datetime, bool]] = []

    def open_count(self) -> int:
        return self._open

    def exits(self) -> list[tuple[datetime, bool]]:
        return self.closed

    def open_exposure(self) -> Decimal:
        return self._exposure


def _gate(**kw: object) -> SlotGate:
    ports = kw.pop("ports", {"A": FakePort()})
    return SlotGate(ports=ports, **kw)  # type: ignore[arg-type]


class TestNotionalRoom:
    """남은 여유 = `cap * slots - held` — 연구 걸음의 `room` 과 같은 식."""

    def test_room_is_cap_times_slots_minus_held(self) -> None:
        assert notional_room(Decimal(5), 6, Decimal(2)) == Decimal(7)

    def test_no_cap_means_no_limit(self) -> None:
        assert notional_room(Decimal(5), 6, None) is None
        assert notional_room(Decimal(5), 0, Decimal(2)) is None
        assert notional_room(Decimal(5), 6, Decimal(0)) is None

    def test_room_can_go_negative_when_already_over(self) -> None:
        # 이미 넘겼으면 음수다 — 문이 이것을 0 이하로 보고 건너뛴다.
        assert notional_room(Decimal(20), 6, Decimal(2)) == Decimal(-8)


class TestDrawdownScale:
    """낙폭 브레이크는 **엄격 부등호**다 — 연구 걸음 `equity < peak * (1 - dd)` 와 같은 자."""

    def test_above_threshold_halves(self) -> None:
        assert drawdown_scale(Decimal("0.13"), Decimal("0.12"), Decimal("0.5")) == Decimal("0.5")

    def test_exactly_at_threshold_does_not_fire(self) -> None:
        # 연구 걸음이 `<` 라 낙폭이 문턱과 **같으면 안 걸린다**. 여기가 갈라지면 매매가 달라진다.
        assert drawdown_scale(Decimal("0.12"), Decimal("0.12"), Decimal("0.5")) == Decimal(1)

    def test_at_peak_is_untouched(self) -> None:
        # 🔴 비대칭 — 고점에서는 키우지 않는다 (146·147차에서 역방향은 ⛔).
        assert drawdown_scale(Decimal(0), Decimal("0.12"), Decimal("0.5")) == Decimal(1)

    def test_disabled_threshold_never_fires(self) -> None:
        assert drawdown_scale(Decimal("0.9"), Decimal(0), Decimal("0.5")) == Decimal(1)


class TestGrantBlocksAllOrNothing:
    """자리·그날 정지는 크기 문제가 아니라 **전부 아니면 전무**다."""

    def test_slots_full_grants_nothing(self) -> None:
        gate = _gate(ports={"A": FakePort(open_count=6)}, slots=6)
        assert gate.grant(AT, Decimal(4)) == (Decimal(0), "slots")

    def test_day_halt_grants_nothing(self) -> None:
        port = FakePort()
        port.closed = [(AT, True), (AT, True)]
        gate = _gate(ports={"A": port}, slots=6, halt_after_stops=2)
        assert gate.grant(AT, Decimal(4)) == (Decimal(0), "day_halt")

    def test_open_gate_grants_what_was_asked(self) -> None:
        assert _gate(slots=6).grant(AT, Decimal(4)) == (Decimal(4), None)

    def test_no_rules_means_no_gate_at_all(self) -> None:
        assert _gate().grant(AT, Decimal(99)) == (Decimal(99), None)


class TestNotionalFit:
    """상한에 걸릴 때 버리느냐 줄이느냐 — 123·124차가 잰 축."""

    def test_without_fit_the_entry_is_thrown_away(self) -> None:
        # 지금까지의 동작. OOS·Gate 매매의 27% 가 여기서 사라지고 있었다.
        gate = _gate(ports={"A": FakePort(exposure=Decimal(10))}, slots=6, notional_cap=Decimal(2))
        assert gate.grant(AT, Decimal(4)) == (Decimal(0), "notional")

    def test_with_fit_the_entry_shrinks_to_the_room(self) -> None:
        # 여유 = 2*6 - 10 = 2 → 4 를 요청하면 2 만 받는다.
        gate = _gate(
            ports={"A": FakePort(exposure=Decimal(10))},
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            min_grant=Decimal(1),
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(2), None)

    def test_crumbs_below_the_floor_are_skipped(self) -> None:
        # 🔴 여유가 기준 배율의 1/4 에 못 미치면 줄이지 않는다 — 부스러기 주문은 거래소
        #    최소 수량에 걸려 예외가 나고, 그 예외는 원장을 쓴 **뒤에** 난다.
        gate = _gate(
            ports={"A": FakePort(exposure=Decimal("11.5"))},
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            min_grant=Decimal(1),
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(0), "notional")

    def test_already_over_the_cap_is_skipped_not_negative(self) -> None:
        gate = _gate(
            ports={"A": FakePort(exposure=Decimal(20))},
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(0), "notional")

    def test_room_to_spare_is_not_shrunk(self) -> None:
        gate = _gate(
            ports={"A": FakePort(exposure=Decimal(2))},
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(4), None)


class TestBrakeInsideTheGate:
    """브레이크는 상한 **앞**이다 — 줄어든 크기가 상한에 안 걸릴 수 있다."""

    def test_brake_halves_the_grant(self) -> None:
        gate = _gate(
            slots=6,
            drawdown=lambda: Decimal("0.20"),
            brake_at=Decimal("0.12"),
            brake_scale=Decimal("0.5"),
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(2), None)

    def test_no_drawdown_means_full_size(self) -> None:
        gate = _gate(
            slots=6,
            drawdown=lambda: Decimal("0.01"),
            brake_at=Decimal("0.12"),
            brake_scale=Decimal("0.5"),
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(4), None)

    def test_brake_runs_before_the_cap(self) -> None:
        """🔴 순서가 이 방향이어야 한다 — 뒤집으면 다른 매매법이다.

        여유 3 · 요청 4 · 브레이크 걸림. 브레이크가 먼저면 4 x 0.5 = 2 로 **상한에 안 걸려**
        2 를 온전히 받는다. 상한이 먼저였다면 3 으로 잘린 뒤 절반이 되어 1.5 를 받는다.
        """
        gate = _gate(
            ports={"A": FakePort(exposure=Decimal(9))},
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            drawdown=lambda: Decimal("0.20"),
            brake_at=Decimal("0.12"),
            brake_scale=Decimal("0.5"),
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(2), None)

    def test_brake_is_read_every_call_not_frozen(self) -> None:
        """낙폭은 틱마다 바뀐다 — 값을 복사해 두면 브레이크가 첫 값에 얼어붙는다."""
        now = {"dd": Decimal(0)}
        gate = _gate(
            slots=6,
            drawdown=lambda: now["dd"],
            brake_at=Decimal("0.12"),
            brake_scale=Decimal("0.5"),
        )
        assert gate.grant(AT, Decimal(4)) == (Decimal(4), None)
        now["dd"] = Decimal("0.30")
        assert gate.grant(AT, Decimal(4)) == (Decimal(2), None)
        now["dd"] = Decimal(0)  # 회복하면 저절로 풀린다 (따로 해제 규칙이 없다)
        assert gate.grant(AT, Decimal(4)) == (Decimal(4), None)


class TestBlocksStaysInSyncWithGrant:
    """`blocks` 는 `grant` 의 사유만 꺼내 쓴다 — 두 벌로 두면 조용히 갈라진다."""

    def test_blocks_mirrors_grant(self) -> None:
        gate = _gate(ports={"A": FakePort(open_count=6)}, slots=6)
        assert gate.blocks(AT) == "slots"

    def test_fit_makes_the_cap_stop_blocking(self) -> None:
        # 줄여서 진입이 켜지면 상한은 더 이상 **막지** 않는다 — 크기만 준다.
        ports = {"A": FakePort(exposure=Decimal(10))}
        assert _gate(ports=ports, slots=6, notional_cap=Decimal(2)).blocks(AT, Decimal(4)) == (
            "notional"
        )
        fit = _gate(
            ports=ports,
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            min_grant=Decimal(1),
        )
        assert fit.blocks(AT, Decimal(4)) is None
