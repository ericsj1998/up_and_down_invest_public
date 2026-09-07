"""상위 TF 는 **닫혀야 읽는다** (T151 · 계획서 §0-3 마지막 항목).

계획서가 *"TF 격자 탐색 시 최다 발생 오류"* 라고 적어 둔 것이다. 4h 봉은 4시간이
지나야 확정인데, 리샘플링한 배열을 인덱스로 읽으면 그 봉이 이미 완성된 것처럼 보인다.

이 시험의 핵심은 **순진한 구현과 나란히 놓는 것**이다 — `i // 15` 가 무엇을 주는지
같이 보여 주지 않으면, 이 규칙이 왜 필요한지 6개월 뒤에 알 수 없다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.clock import (
    Moment,
    align,
    confirmed_at,
    is_confirmed,
    latest_confirmed,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 30, hour, minute, tzinfo=UTC)


def series(start: datetime, step: timedelta, count: int) -> list[datetime]:
    return [start + step * i for i in range(count)]


class TestConfirmation:
    def test_a_bar_confirms_when_it_closes(self) -> None:
        assert confirmed_at(at(8), Timeframe.H4) == at(12)

    def test_the_closing_instant_counts_as_confirmed(self) -> None:
        """⚠️ 경계는 확정 쪽이다 — 12:00 정각에 [08:00,12:00) 종가는 이미 사실이다."""
        assert is_confirmed(at(8), Timeframe.H4, at=at(12))

    def test_one_second_earlier_is_not(self) -> None:
        assert not is_confirmed(at(8), Timeframe.H4, at=at(12) - timedelta(seconds=1))

    def test_naive_time_raises(self) -> None:
        with pytest.raises(ValueError, match="UTC aware"):
            confirmed_at(datetime(2026, 8, 30, 8), Timeframe.H4)


class TestLatestConfirmed:
    def test_it_is_none_before_the_first_close(self) -> None:
        """🔴 `-1` 이면 파이썬이 **마지막 원소**를 준다 — 구간 끝의 미래 봉이다."""
        bars = series(at(0), timedelta(hours=4), 6)
        assert latest_confirmed(bars, Timeframe.H4, at=at(3)) is None

    def test_it_points_at_the_bar_that_already_closed(self) -> None:
        bars = series(at(0), timedelta(hours=4), 6)
        # 09:00 에 서 있다 → [04:00,08:00) 까지만 읽을 수 있다 (인덱스 1).
        assert latest_confirmed(bars, Timeframe.H4, at=at(9)) == 1


class TestAlign:
    def test_it_never_returns_the_bar_being_formed(self) -> None:
        """🔴 이 시험이 계획서 §0-3 마지막 항목 그 자체다.

        1분봉 09:03 에 서 있을 때 읽을 수 있는 4h 봉은 [04:00,08:00) 이지, 지금
        만들어지는 중인 [08:00,12:00) 이 아니다.
        """
        lower = series(at(9), timedelta(minutes=1), 5)
        higher = series(at(0), timedelta(hours=4), 4)
        got = align(lower, Timeframe.M1, higher, Timeframe.H4)

        assert got == [1, 1, 1, 1, 1]
        assert higher[1] == at(4), "읽는 봉은 [04:00,08:00)"
        # 지금 만들어지는 중인 봉은 [08:00,12:00) 이고, 그것은 아직 확정이 아니다.
        assert not is_confirmed(higher[2], Timeframe.H4, at=lower[0])

    def test_open_sees_one_less_than_close(self) -> None:
        """⚠️ 시가 시점과 종가 시점은 **한 봉 차이**가 날 수 있다.

        15분봉 09:15 봉을 보자. 그 봉의 **시가**에 서면 [09:00,09:15) 은 방금 닫혔다.
        하지만 09:00 봉의 시가에 서면 아직 [08:45,09:00) 까지만 읽을 수 있다.
        """
        lower = series(at(9), timedelta(minutes=15), 3)
        higher = series(at(8, 45), timedelta(minutes=15), 3)

        on_open = align(lower, Timeframe.M15, higher, Timeframe.M15, moment=Moment.OPEN)
        on_close = align(lower, Timeframe.M15, higher, Timeframe.M15, moment=Moment.CLOSE)

        assert on_open == [0, 1, 2]
        assert on_close == [1, 2, 2]

    def test_none_until_the_first_higher_bar_closes(self) -> None:
        lower = series(at(0), timedelta(minutes=1), 3)
        higher = series(at(0), timedelta(hours=4), 2)
        assert align(lower, Timeframe.M1, higher, Timeframe.H4) == [None, None, None]

    def test_duplicate_timestamps_raise(self) -> None:
        """계획서 §0-1: 중복 타임스탬프는 **제거 대상**이다 — 조용히 정렬하지 않는다."""
        bad = [at(1), at(1), at(2)]
        with pytest.raises(ValueError, match="오름차순"):
            align(bad, Timeframe.H1, series(at(0), timedelta(hours=4), 2), Timeframe.H4)

    def test_backwards_timestamps_raise(self) -> None:
        bad = [at(2), at(1)]
        with pytest.raises(ValueError, match="오름차순"):
            align(series(at(0), timedelta(hours=1), 2), Timeframe.H1, bad, Timeframe.H4)


class TestTheNaiveVersionLeaks:
    """⭐ 규칙의 **효과**를 잰다 — 없으면 몇 봉이나 미래를 보나."""

    def test_index_division_reads_an_unclosed_bar(self) -> None:
        lower = series(at(0), timedelta(minutes=1), 240)
        higher = series(at(0), timedelta(hours=4), 2)
        safe = align(lower, Timeframe.M1, higher, Timeframe.H4)

        leaked = 0
        for index, ts in enumerate(lower):
            naive = index // 240  # "지금 봉이 속한 4h 봉" — 흔한 구현
            if safe[index] != naive:
                leaked += 1
                # 순진한 쪽이 가리키는 봉은 **아직 안 닫혔다**.
                assert not is_confirmed(higher[naive], Timeframe.H4, at=ts)

        # ⭐ 240 이 아니라 239 다. **마지막 1분봉의 종가가 정확히 04:00** 이고, 그
        #    순간 4h 봉은 막 닫혔다 — 거기서만 두 구현이 우연히 같은 답을 낸다.
        #    나머지 4시간 내내 순진한 쪽은 미완성 봉을 읽는다.
        assert leaked == 239
