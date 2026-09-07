"""데이터 위생 — **고쳐 주지 않는다** (T152 §3).

결측을 앞 값으로 채우면 없던 가격이 생기고 그 가격에 신호가 걸린다. 급락을 지우면
백테스트가 예뻐지고 **하드 제약(청산)이 과소평가**된다.
"""

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.hygiene import inspect, keep_indices

MINUTE = 60_000


def minutes(count: int, *, start: int = 0) -> list[int]:
    return [start + MINUTE * i for i in range(count)]


def flat(count: int, value: float = 100.0) -> list[float]:
    return [value] * count


class TestGaps:
    def test_a_clean_series_reports_nothing(self) -> None:
        ts = minutes(60)
        got = inspect(ts, flat(60), flat(60), flat(60), flat(60), timeframe=Timeframe.M1)
        assert got.clean()
        assert got.coverage == 1.0

    def test_a_missing_minute_is_counted_not_filled(self) -> None:
        """🔴 채우지 않는다. **몇 개가 비었는지** 만 센다."""
        ts = [*minutes(10), *minutes(10, start=15 * MINUTE)]
        size = len(ts)
        got = inspect(ts, flat(size), flat(size), flat(size), flat(size), timeframe=Timeframe.M1)
        assert got.missing == 5
        assert got.bars == 20
        assert got.expected == 25
        assert got.coverage == 0.8

    def test_gap_lengths_are_reported_as_a_distribution(self) -> None:
        """⭐ 문턱이 말이 되는지 보려면 **분포**가 있어야 한다 (관측 규약 §1-0s)."""
        ts = [0, 2 * MINUTE, 4 * MINUTE, 20 * MINUTE]
        got = inspect(ts, flat(4), flat(4), flat(4), flat(4), timeframe=Timeframe.M1)
        assert got.gap_sizes == {1: 2, 15: 1}

    def test_long_gaps_become_halt_candidates(self) -> None:
        ts = [0, MINUTE, 60 * MINUTE]
        got = inspect(ts, flat(3), flat(3), flat(3), flat(3), timeframe=Timeframe.M1, halt_bars=10)
        assert len(got.halts) == 1
        assert got.halts[0].missing == 58

    def test_the_interval_comes_from_the_timeframe_not_the_data(self) -> None:
        """🔴 데이터에서 간격을 추측하면 결측투성이 종목이 **결측 0** 으로 보고된다.

        아래는 2분마다 한 봉이다. 최빈 간격을 추측하면 2분이 정상이 되어 결측이
        사라진다 — 가장 나쁜 종류의 조용한 실패다.
        """
        ts = [MINUTE * 2 * i for i in range(10)]
        got = inspect(ts, flat(10), flat(10), flat(10), flat(10), timeframe=Timeframe.M1)
        assert got.missing == 9


class TestDuplicatesAndBackwards:
    def test_duplicates_are_counted(self) -> None:
        ts = [0, MINUTE, MINUTE, 2 * MINUTE]
        got = inspect(ts, flat(4), flat(4), flat(4), flat(4), timeframe=Timeframe.M1)
        assert got.duplicates == 1
        assert not got.clean()

    def test_backwards_is_counted_separately(self) -> None:
        """⚠️ 역행은 중복과 다르다 — 파일이 깨졌다는 뜻이라 조용히 정렬하면 안 된다."""
        ts = [0, 2 * MINUTE, MINUTE]
        got = inspect(ts, flat(3), flat(3), flat(3), flat(3), timeframe=Timeframe.M1)
        assert got.backwards == 1
        assert got.duplicates == 0

    def test_keep_indices_keeps_the_first(self) -> None:
        assert keep_indices([0, MINUTE, MINUTE, 2 * MINUTE]) == [0, 1, 3]

    def test_keep_indices_does_not_sort(self) -> None:
        """🔴 정렬해 주면 파일이 깨진 것을 모른다."""
        assert keep_indices([2 * MINUTE, MINUTE, 0]) == [0, 1, 2]


class TestSpikesAreMarkedNotRemoved:
    def test_a_flash_crash_is_reported(self) -> None:
        """⛔ 지우지 않는다 — 청산 판정의 진짜 재료다."""
        ts = minutes(3)
        got = inspect(
            ts,
            [100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0],
            [100.0, 80.0, 100.0],
            [100.0, 90.0, 100.0],
            timeframe=Timeframe.M1,
            spike_pct=5.0,
        )
        assert len(got.spikes) == 1
        assert got.spikes[0].index == 1
        assert got.spikes[0].move_pct == -10.0
        assert got.spikes[0].range_pct == 20.0

    def test_the_series_is_unchanged(self) -> None:
        """⚠️ 보고서는 인덱스만 든다. 원본 열을 건드리는 API 가 아예 없다."""
        ts = minutes(3)
        low = [100.0, 80.0, 100.0]
        inspect(ts, flat(3), flat(3), low, flat(3), timeframe=Timeframe.M1)
        assert low == [100.0, 80.0, 100.0]

    def test_a_wide_range_counts_even_without_a_close_move(self) -> None:
        """⭐ 시가=종가라도 봉 안에서 20% 를 오갔으면 그것이 청산이 나는 자리다."""
        ts = minutes(1)
        got = inspect(ts, [100.0], [110.0], [90.0], [100.0], timeframe=Timeframe.M1, spike_pct=5.0)
        assert len(got.spikes) == 1
        assert got.spikes[0].move_pct == 0.0


class TestEmpty:
    def test_an_empty_series_is_not_a_crash(self) -> None:
        got = inspect([], [], [], [], [], timeframe=Timeframe.M1)
        assert got.bars == 0
        assert got.coverage == 0.0
