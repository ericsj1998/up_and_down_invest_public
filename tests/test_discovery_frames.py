"""리샘플링 — 경계는 **시각**이고, 양 끝의 잘린 통은 버린다 (T152 §4).

오늘 이미 한 번 틀렸다: 인덱스로 칸을 자르니 설계칸이 "매매 0건" 으로 나오고
검증칸이 150일을 가리켰다. 조용히 틀린 구간을 재고 있었다.
"""

from datetime import UTC, datetime

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.frames import resample

MINUTE = 60_000


def minutes(count: int, *, start: int = 0) -> list[int]:
    return [start + MINUTE * i for i in range(count)]


def flat(count: int, base: float = 100.0) -> list[float]:
    return [base + i for i in range(count)]


class TestBoundariesAreTimes:
    def test_buckets_land_on_epoch_multiples(self) -> None:
        """⭕ `(ts // span) * span` — 거래소 봉 경계와 같은 규칙이다."""
        ts = minutes(30)
        frame, _ = resample(
            ts,
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert list(frame.ts) == [0, 15 * MINUTE]

    def test_a_series_starting_mid_bucket_does_not_shift_the_grid(self) -> None:
        """🔴 인덱스로 잘랐다면 여기서 격자가 통째로 밀린다.

        07분에 시작해도 15분봉 경계는 00·15·30 이다 — 07·22·37 이 아니다.
        """
        ts = minutes(40, start=7 * MINUTE)
        frame, dropped = resample(
            ts,
            flat(40),
            flat(40),
            flat(40),
            flat(40),
            flat(40),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        # 첫 통 [00,15) 은 07 부터만 있어 **잘렸다** → 버려진다.
        assert dropped.head == 1
        assert next(iter(frame.ts)) == 15 * MINUTE

    def test_ohlc_folds_correctly(self) -> None:
        ts = minutes(15)
        open_ = [10.0] + [11.0] * 14
        high = [12.0] * 7 + [20.0] + [12.0] * 7
        low = [9.0] * 3 + [1.0] + [9.0] * 11
        close = [11.0] * 14 + [17.0]
        volume = [2.0] * 15
        frame, _ = resample(
            ts,
            open_,
            high,
            low,
            close,
            volume,
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert frame.open[0] == 10.0
        assert frame.high[0] == 20.0
        assert frame.low[0] == 1.0
        assert frame.close[0] == 17.0
        assert frame.volume[0] == 30.0
        assert frame.sources[0] == 15


class TestPartialEdgesAreDropped:
    def test_a_truncated_tail_is_dropped(self) -> None:
        """🔴 마지막 통이 5분만 들어 있으면 그 종가는 15분봉 종가가 아니다."""
        ts = minutes(20)
        frame, dropped = resample(
            ts,
            flat(20),
            flat(20),
            flat(20),
            flat(20),
            flat(20),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert dropped.tail == 1
        assert len(frame) == 1

    def test_a_complete_series_drops_nothing(self) -> None:
        ts = minutes(30)
        frame, dropped = resample(
            ts,
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert dropped.head == 0
        assert dropped.tail == 0
        assert len(frame) == 2

    def test_a_gap_in_the_middle_is_kept(self) -> None:
        """⚠️ 가운데의 얇은 통은 **결측**이지 불완전이 아니다 — 지우면 없던 연속성이 생긴다."""
        ts = [*minutes(15), *minutes(15, start=30 * MINUTE)]
        size = len(ts)
        frame, dropped = resample(
            ts,
            flat(size),
            flat(size),
            flat(size),
            flat(size),
            flat(size),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert dropped.head == 0 and dropped.tail == 0
        assert list(frame.ts) == [0, 30 * MINUTE]
        assert list(frame.sources) == [15, 15], "가운데 [15,30) 은 아예 없다 — 만들지 않는다"


class TestConfirmationTravelsWithTheBar:
    def test_it_reuses_the_one_definition(self) -> None:
        """🔴 확정 시각을 여기서 다시 계산하면 상위 축만 조용히 미래를 읽는다."""
        ts = minutes(30)
        frame, _ = resample(
            ts,
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert frame.confirmed_at(0) == datetime(1970, 1, 1, 0, 15, tzinfo=UTC)

    def test_times_are_utc_aware(self) -> None:
        ts = minutes(30)
        frame, _ = resample(
            ts,
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            flat(30),
            source=Timeframe.M1,
            target=Timeframe.M15,
        )
        assert all(one.tzinfo is not None for one in frame.times())


class TestItRefusesBadInput:
    def test_backwards_time_raises(self) -> None:
        ts = [0, 2 * MINUTE, MINUTE]
        with pytest.raises(ValueError, match="오름차순"):
            resample(
                ts,
                flat(3),
                flat(3),
                flat(3),
                flat(3),
                flat(3),
                source=Timeframe.M1,
                target=Timeframe.M15,
            )

    def test_duplicate_time_raises(self) -> None:
        ts = [0, MINUTE, MINUTE]
        with pytest.raises(ValueError, match="오름차순"):
            resample(
                ts,
                flat(3),
                flat(3),
                flat(3),
                flat(3),
                flat(3),
                source=Timeframe.M1,
                target=Timeframe.M15,
            )

    def test_a_non_multiple_target_raises(self) -> None:
        """⚠️ 배수가 아니면 통이 원본 봉을 쪼개게 되고, 그 봉은 어느 통에도 온전히 안 든다."""
        ts = minutes(30)
        with pytest.raises(ValueError, match="배수가 아니다"):
            resample(
                ts,
                flat(30),
                flat(30),
                flat(30),
                flat(30),
                flat(30),
                source=Timeframe.M15,
                target=Timeframe.M1,
            )


class TestTheGrid:
    @pytest.mark.parametrize(
        ("target", "span"),
        [
            (Timeframe.M5, 5),
            (Timeframe.M15, 15),
            (Timeframe.M30, 30),
            (Timeframe.H1, 60),
            (Timeframe.H4, 240),
            (Timeframe.D1, 1440),
        ],
    )
    def test_every_axis_folds_the_expected_count(self, target: Timeframe, span: int) -> None:
        """격자의 **모든 칸**에서 성립해야 한다 — 한 축만 어긋나도 그 축만 다른 기간을 잰다."""
        count = 1440 * 4
        ts = minutes(count)
        frame, dropped = resample(
            ts,
            flat(count),
            flat(count),
            flat(count),
            flat(count),
            flat(count),
            source=Timeframe.M1,
            target=target,
        )
        assert dropped.head == 0 and dropped.tail == 0
        assert len(frame) == count // span
        assert all(one == span for one in frame.sources)
