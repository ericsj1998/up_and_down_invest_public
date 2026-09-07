"""조합 — 게이트는 **먼저** 켜져 있어야 한다 (T155).

조합에서 미래 참조가 들어오는 가장 흔한 자리다. *"둘이 같이 났다"* 를 시각 무시하고
세면 뒤에 켜진 게이트로 앞의 진입을 고른 것이 된다.
"""

import pytest

from updown.orchestration.discovery.combine import gate
from updown.orchestration.discovery.signals.base import Trigger
from updown.orchestration.walkforward.ledger import Direction

LONG = Direction.LONG
SHORT = Direction.SHORT


def marks(*rows: tuple[int, Direction]) -> list[Trigger]:
    return [Trigger(index=index, direction=way) for index, way in rows]


class TestTheGateMustComeFirst:
    def test_an_earlier_gate_passes(self) -> None:
        got = gate(marks((100, LONG)), marks((95, LONG)), window=12)
        assert len(got) == 1

    def test_a_later_gate_does_not(self) -> None:
        """🔴 그 시점에 없던 정보다 (계획서 §0-3)."""
        got = gate(marks((100, LONG)), marks((105, LONG)), window=12)
        assert len(got) == 0

    def test_the_same_bar_passes(self) -> None:
        """⭐ 둘 다 그 봉의 종가로 판정되고 체결은 다음 봉이다 — 미래 참조가 아니다."""
        got = gate(marks((100, LONG)), marks((100, LONG)), window=12)
        assert len(got) == 1

    def test_a_stale_gate_expires(self) -> None:
        got = gate(marks((100, LONG)), marks((80, LONG)), window=12)
        assert len(got) == 0

    def test_the_window_edge_is_inclusive(self) -> None:
        assert len(gate(marks((100, LONG)), marks((88, LONG)), window=12)) == 1
        assert len(gate(marks((100, LONG)), marks((87, LONG)), window=12)) == 0


class TestDirection:
    def test_opposite_directions_are_rejected_by_default(self) -> None:
        got = gate(marks((100, LONG)), marks((95, SHORT)), window=12)
        assert len(got) == 0

    def test_direction_can_be_ignored(self) -> None:
        """⚠️ 변동성 신호처럼 **방향이 없는** 조건에 쓴다."""
        got = gate(marks((100, LONG)), marks((95, SHORT)), window=12, same_direction=False)
        assert len(got) == 1

    def test_it_looks_past_a_wrong_direction_gate(self) -> None:
        """창 안에 맞는 방향이 하나라도 있으면 통과한다."""
        got = gate(marks((100, LONG)), marks((94, LONG), (95, SHORT)), window=12)
        assert len(got) == 1


class TestSampleReduction:
    def test_it_reports_what_was_lost(self) -> None:
        """🔴 표본이 94% 사라지면서 합계가 좋아지는 것을 개선으로 착각한 적이 있다."""
        primary = marks(*[(index, LONG) for index in range(0, 1000, 10)])
        condition = marks((0, LONG), (500, LONG))
        got = gate(primary, condition, window=12)
        assert got.before == 100
        assert got.kept < 0.1
        assert len(got) == pytest.approx(4, abs=2)

    def test_a_permissive_gate_keeps_everything(self) -> None:
        primary = marks(*[(index, LONG) for index in range(50)])
        condition = marks(*[(index, LONG) for index in range(50)])
        got = gate(primary, condition, window=12)
        assert got.kept == 1.0

    def test_an_empty_primary_is_not_a_crash(self) -> None:
        got = gate([], marks((5, LONG)), window=12)
        assert len(got) == 0
        assert got.kept == 0.0

    def test_an_empty_gate_blocks_everything(self) -> None:
        got = gate(marks((100, LONG)), [], window=12)
        assert len(got) == 0
        assert got.before == 1


class TestItRefusesBadInput:
    def test_a_zero_window_raises(self) -> None:
        with pytest.raises(ValueError, match="창은"):
            gate(marks((1, LONG)), marks((0, LONG)), window=0)

    def test_unsorted_input_raises(self) -> None:
        """⚠️ 이분 탐색을 쓰므로 순서가 어긋나면 조용히 틀린다."""
        with pytest.raises(ValueError, match="오름차순"):
            gate(marks((5, LONG), (1, LONG)), marks((0, LONG)), window=12)
