"""동시 포지션 1개 — 격자 판정의 전제 (오더 1-D).

🔴 이 제약이 판정을 뒤집는다. 후보 10칸을 겹침 허용으로 재면 초과수익 +0.18~0.27%
였는데, 1개 제약을 걸자 **전부 Net 음수**였고 일곱은 무작위보다 나빴다.

여기서 못 박는 것:

    선입 우선 · 같은 시각이면 종목 이름 오름차순 (결정론)
    청산 시각 = 진입 + 지평 (지평 청산이라 고정)
    잡은 것 + 버린 것 = 전체
"""

from __future__ import annotations

import pytest

from updown.orchestration.discovery.cache import Fired
from updown.orchestration.discovery.solo import serialize
from updown.orchestration.walkforward.ledger import Direction

MINUTE = 60_000


def fired(*minutes: int) -> list[Fired]:
    """분 단위 시각으로 방아쇠 목록."""
    return [
        Fired(index=index, direction=Direction.LONG, ts=one * MINUTE)
        for index, one in enumerate(minutes)
    ]


class TestItKeepsOnlyOneAtATime:
    def test_overlapping_triggers_are_dropped(self) -> None:
        # 지평 60분. 0·10·20분 방아쇠 → 진입 0·10·20 이므로 첫 것만 잡는다.
        found = serialize({"AAA": fired(0, 10, 20)}, horizon_minutes=60, entry_lag_ms=0)
        assert found.kept == 1
        assert found.dropped == 2
        assert found.has("AAA", 0)
        assert not found.has("AAA", 1)

    def test_a_gap_wider_than_the_horizon_keeps_both(self) -> None:
        found = serialize({"AAA": fired(0, 60, 120)}, horizon_minutes=60, entry_lag_ms=0)
        assert found.kept == 3

    def test_kept_plus_dropped_is_everything(self) -> None:
        streams = {"AAA": fired(0, 5, 10, 70, 75), "BBB": fired(1, 2, 65, 200)}
        found = serialize(streams, horizon_minutes=60, entry_lag_ms=0)
        assert found.kept + found.dropped == 9


class TestItIsDeterministic:
    """🔴 같은 입력에서 같은 답 — 절대 규칙 #5."""

    def test_a_tie_goes_to_the_earlier_symbol_name(self) -> None:
        streams = {"ZZZ": fired(0), "AAA": fired(0)}
        found = serialize(streams, horizon_minutes=60, entry_lag_ms=0)
        assert found.has("AAA", 0)
        assert not found.has("ZZZ", 0)

    def test_dictionary_order_does_not_change_the_answer(self) -> None:
        first = serialize(
            {"AAA": fired(0, 30), "BBB": fired(0, 30)}, horizon_minutes=60, entry_lag_ms=0
        )
        second = serialize(
            {"BBB": fired(0, 30), "AAA": fired(0, 30)}, horizon_minutes=60, entry_lag_ms=0
        )
        assert first.taken["AAA"] == second.taken["AAA"]
        assert first.taken["BBB"] == second.taken["BBB"]


class TestTheEntryLagCounts:
    """⚠️ 자리를 차지하는 것은 방아쇠가 아니라 **진입**이다 (다음 봉 시가)."""

    def test_the_lag_shifts_the_occupancy_window(self) -> None:
        # 지평 10분 · 진입 지연 5분. 0분 방아쇠 → 진입 5분 → 15분에 비운다.
        # 10분 방아쇠 → 진입 15분 → 비는 순간이라 잡힌다.
        found = serialize({"AAA": fired(0, 10)}, horizon_minutes=10, entry_lag_ms=5 * MINUTE)
        assert found.kept == 2

    def test_without_the_lag_the_same_pair_collides(self) -> None:
        found = serialize({"AAA": fired(0, 5)}, horizon_minutes=10, entry_lag_ms=0)
        assert found.kept == 1


class TestItRefusesNonsense:
    def test_a_zero_horizon_raises(self) -> None:
        with pytest.raises(ValueError, match="지평은 양수"):
            serialize({"AAA": fired(0)}, horizon_minutes=0, entry_lag_ms=0)

    def test_no_triggers_is_empty_not_an_error(self) -> None:
        found = serialize({"AAA": []}, horizon_minutes=60, entry_lag_ms=0)
        assert found.kept == 0
        assert found.dropped == 0


class TestTheConstraintBitesHardestAtLongHorizons:
    """⭐ 지평이 길수록 더 많이 버린다 — 3일 지평 칸이 왜 무너졌는지의 산수다."""

    def test_a_longer_horizon_drops_more(self) -> None:
        stream = {"AAA": fired(*range(0, 600, 10))}
        short = serialize(stream, horizon_minutes=10, entry_lag_ms=0)
        long = serialize(stream, horizon_minutes=120, entry_lag_ms=0)
        assert long.kept < short.kept
        assert short.kept == 60
        assert long.kept == 5
