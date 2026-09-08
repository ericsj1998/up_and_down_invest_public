"""라이브 급전 (`orchestration/walkforward/live_feed.py`).

지키려는 성질 셋 — 셋 다 **깨져도 예외가 안 나는** 종류다.

1. **미마감 봉이 시리즈로 새지 않는다.** 새면 같은 시각의 봉이 여러 값으로 들어가고
   지표가 매 틱 흔들린다. 백테스트에는 없는 상태라 라이브만 다른 판단을 한다.
2. **구멍이 조용히 지나가지 않는다.** 빈 봉은 "거래가 없었다" 와 구별되지 않는다.
3. **커서가 뒤로 가지 않는다.** 되돌리면 이미 본 봉을 다시 판정한다 (미래 참조와 같다).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.orchestration.walkforward.live_feed import LiveFeed, LiveFeedError
from updown.orchestration.walkforward.sealed import SealBreachError

BTC = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="BTC 무기한",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
ORIGIN = datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


def bar(index: int, close: str = "100") -> Candle:
    """15m 봉 하나."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M15,
        ts=ORIGIN + STEP * index,
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=Decimal(close),
        volume=Decimal(1),
    )


def feed(count: int = 3) -> LiveFeed:
    """시드 `count` 봉짜리 급전."""
    return LiveFeed({Timeframe.M15: [bar(i) for i in range(count)]}, Timeframe.M15)


class TestSeed:
    def test_the_cursor_sits_after_the_last_seed_bar(self) -> None:
        """🔴 마지막 봉의 `ts` 가 아니라 그 **다음** 시작이다.

        `ts` 로 두면 `view` 가 그 봉을 미래로 보고 잘라낸다 — 마감된 봉이 안 보이는
        급전이 된다.
        """
        live = feed(3)
        assert live.cursor == ORIGIN + STEP * 3
        assert len(live.judged(Timeframe.M15)) == 3

    def test_an_empty_seed_raises(self) -> None:
        """⛔ 걸어가다 빈 봉을 만나면 "기회가 없었다" 로 읽힌다 — 지금 터뜨린다."""
        with pytest.raises(LiveFeedError, match="비었다"):
            LiveFeed({Timeframe.M15: []}, Timeframe.M15)

    def test_a_missing_entry_frame_raises(self) -> None:
        """커서를 전진시킬 단위가 없으면 급전이 성립하지 않는다."""
        with pytest.raises(LiveFeedError, match="진입 시간축"):
            LiveFeed({Timeframe.M15: [bar(0)]}, Timeframe.H1)


class TestPendingDoesNotLeak:
    """🔴 성질 ① — 미마감 봉이 시리즈로 새지 않는다."""

    def test_an_open_bar_is_not_in_the_view(self) -> None:
        live = feed(3)
        moved = live.push(Timeframe.M15, bar(3, "999"), closed=False)
        assert moved is False
        assert len(live.judged(Timeframe.M15)) == 3
        closes = [item.close for item in live.judged(Timeframe.M15)]
        assert Decimal(999) not in closes

    def test_an_open_bar_is_visible_separately(self) -> None:
        """⚠️ 버리지 않는다 — 화면은 진행 중인 봉을 보여줘야 한다 (그게 라이브다)."""
        live = feed(3)
        live.push(Timeframe.M15, bar(3, "999"), closed=False)
        held = live.pending(Timeframe.M15)
        assert held is not None
        assert held.close == Decimal(999)

    def test_an_open_bar_does_not_move_the_cursor(self) -> None:
        """커서가 움직이면 세션이 그 봉으로 판정한다."""
        live = feed(3)
        before = live.cursor
        live.push(Timeframe.M15, bar(3), closed=False)
        assert live.cursor == before

    def test_closing_the_bar_clears_pending(self) -> None:
        """같은 봉이 마감되면 진행 중 목록에서 빠진다 — 안 빠지면 화면이 두 번 그린다."""
        live = feed(3)
        live.push(Timeframe.M15, bar(3, "999"), closed=False)
        live.push(Timeframe.M15, bar(3, "500"), closed=True)
        assert live.pending(Timeframe.M15) is None
        assert live.judged(Timeframe.M15)[-1].close == Decimal(500)


class TestClosedBars:
    def test_a_closed_bar_enters_and_moves_the_cursor(self) -> None:
        live = feed(3)
        assert live.push(Timeframe.M15, bar(3), closed=True) is True
        assert live.cursor == ORIGIN + STEP * 4
        assert len(live.judged(Timeframe.M15)) == 4

    def test_the_later_value_wins_for_the_same_bar(self) -> None:
        """⭐ REST 시드와 웹소켓이 겹칠 수 있고, 나중 값이 확정값이다."""
        live = feed(3)
        live.push(Timeframe.M15, bar(2, "777"), closed=True)
        assert live.judged(Timeframe.M15)[2].close == Decimal(777)

    def test_a_repeated_bar_does_not_move_the_cursor_backwards(self) -> None:
        """🔴 성질 ③ — 커서는 뒤로 가지 않는다."""
        live = feed(3)
        live.push(Timeframe.M15, bar(3), closed=True)
        after = live.cursor
        live.push(Timeframe.M15, bar(1, "1"), closed=True)
        assert live.cursor == after

    def test_advance_answers_for_any_frame_the_session_asks(self) -> None:
        """🔴 세션은 `STEP_FRAME`(5m)으로 묻는데 진입축은 15m 이다.

        실주행 40분에서 이것 때문에 **걸음이 0** 이었다 — 봉은 흘렀고(799→802) 구멍도
        재연결도 없는데 세션이 한 번도 판정하지 않았다. 예외가 안 나는 종류라 조립해서
        돌려 보고서야 알았다.

        ⛔ `STEP_FRAME` 은 세션(매매 로직)이라 못 바꾼다. 급전이 맞춘다.
        """
        from updown.orchestration.walkforward.session import STEP_FRAME

        live = feed(3)
        assert STEP_FRAME is not Timeframe.M15, "이 테스트의 전제가 사라졌다"
        live.push(Timeframe.M15, bar(3), closed=True)
        assert live.advance(STEP_FRAME) is True, "세션이 묻는 축으로도 답해야 한다"
        assert live.advance(STEP_FRAME) is False, "소비하면 내려간다"

    def test_advance_is_false_without_a_new_bar(self) -> None:
        """⛔ 새 봉 없이 True 를 주면 같은 봉을 두 번 판정한다 — 주문이 두 번 난다."""
        from updown.orchestration.walkforward.session import STEP_FRAME

        live = feed(3)
        assert live.advance(STEP_FRAME) is False, "시드는 이미 판정된 것으로 본다"

    def test_a_higher_frame_does_not_move_the_cursor(self) -> None:
        """전진 단위는 진입 시간축 하나다 — 1h 봉이 커서를 15m 만큼 밀면 안 된다."""
        live = LiveFeed(
            {Timeframe.M15: [bar(0)], Timeframe.H1: [bar(0)]},
            Timeframe.M15,
        )
        before = live.cursor
        assert live.push(Timeframe.H1, bar(4), closed=True) is False
        assert live.cursor == before

    def test_an_unknown_frame_raises(self) -> None:
        with pytest.raises(KeyError):
            feed().push(Timeframe.H4, bar(3), closed=True)


class TestGaps:
    """🔴 성질 ② — 구멍이 조용히 지나가지 않는다."""

    def test_a_skipped_bar_is_counted(self) -> None:
        live = feed(3)
        live.push(Timeframe.M15, bar(5), closed=True)  # 3, 4 가 빠졌다
        assert live.gaps == 2

    def test_a_continuous_stream_has_no_gaps(self) -> None:
        live = feed(3)
        for index in (3, 4, 5):
            live.push(Timeframe.M15, bar(index), closed=True)
        assert live.gaps == 0

    def test_backfill_reduces_the_count(self) -> None:
        """⭐ 메운 만큼 줄인다 — 안 줄이면 "구멍이 있다" 가 영구히 남는다."""
        live = feed(3)
        live.push(Timeframe.M15, bar(5), closed=True)
        assert live.gaps == 2
        assert live.backfill(Timeframe.M15, [bar(3), bar(4)]) == 2
        assert live.gaps == 0
        assert len(live.judged(Timeframe.M15)) == 6

    def test_backfill_does_not_double_count(self) -> None:
        """이미 있는 봉은 새로 채운 것이 아니다."""
        live = feed(3)
        assert live.backfill(Timeframe.M15, [bar(0), bar(1)]) == 0

    def test_backfill_does_not_move_the_cursor(self) -> None:
        """⚠️ 과거를 채우는 일이다. 커서를 뒤로 돌리면 이미 본 봉을 다시 판정한다."""
        live = feed(3)
        live.push(Timeframe.M15, bar(5), closed=True)
        after = live.cursor
        live.backfill(Timeframe.M15, [bar(3), bar(4)])
        assert live.cursor == after


class TestSameFaceAsSealed:
    """`Session` 이 두 급전을 구별하지 않아야 한다 (원칙 P3)."""

    def test_asking_for_the_future_raises_the_same_error(self) -> None:
        """🔴 `SealBreachError` 를 쓴다 — 라이브용 예외를 따로 만들면 상위에 분기가
        생기고, 그 분기가 "라이브에서만 나는 버그" 의 자리가 된다.
        """
        live = feed(3)
        with pytest.raises(SealBreachError, match="미래"):
            live.judged(Timeframe.M15, at=live.cursor + STEP)

    def test_rewinding_is_allowed(self) -> None:
        """되감기는 보기만 하는 것이므로 허용한다 — `SealedFeed` 와 같다."""
        live = feed(3)
        assert len(live.judged(Timeframe.M15, at=ORIGIN + STEP * 2)) == 2

    def test_live_never_finishes(self) -> None:
        """⛔ 시장은 계속 돈다. True 를 주면 러너가 멈추고 라이브가 조용히 죽는다."""
        live = feed(3)
        live.push(Timeframe.M15, bar(3), closed=True)
        assert live.finished is False

    def test_progress_is_zero_not_one(self) -> None:
        """끝이 없으니 비율이 성립하지 않는다. 1.0 을 주면 화면이 "끝났다" 로 그린다."""
        assert feed(3).progress() == 0.0


class TestSealIsHonest:
    """🔴 라이브 `seal` 은 **볼 수 있는 구간**이다 — 가짜가 아니다.

    이것이 있어야 `Session.feed` 주석을 `Feed` 프로토콜로 넓힐 수 있다. API 층이
    `feed.seal` 로 화면 구간을 그리므로(정당한 결합), 라이브에 그 개념이 없으면
    16곳이 깨진다.
    """

    def test_it_spans_seed_start_to_cursor(self) -> None:
        live = feed(3)
        assert live.seal.start == ORIGIN
        assert live.seal.end == live.cursor

    def test_the_end_grows_with_new_bars(self) -> None:
        """⚠️ 끝이 자란다 — 화면이 캐시하면 낡는다."""
        live = feed(3)
        before = live.seal.end
        live.push(Timeframe.M15, bar(3), closed=True)
        assert live.seal.end > before

    def test_the_end_is_not_a_fake_future(self) -> None:
        """⛔ 먼 미래로 두면 진행률이 0 에 붙어 화면이 "시작도 안 했다" 로 보인다."""
        live = feed(3)
        assert live.seal.end == live.cursor

    def test_the_future_is_still_refused(self) -> None:
        """`Seal` 의 뜻이 "이 밖으로는 못 간다" 이고 라이브도 그렇다 — 그래서 정직하다."""
        live = feed(3)
        with pytest.raises(SealBreachError):
            live.judged(Timeframe.M15, at=live.seal.end + STEP)
