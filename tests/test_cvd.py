"""CVD 확인 (T28) — 가격이 신고가일 때 CVD 도 신고가인가.

🔴 이 파일에는 **사고 재발 방지 시험**도 있다: `context()` 가 부르는 세션 메서드가
실제로 있는지. 2026-08-22 에 그 메서드를 안 만든 채 컨텍스트만 고쳐서 라이브 판
5개가 6분간 멈췄다 (`'Session' object has no attribute '_bar_deltas'`).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.indicators.cvd import CvdSeries, cvd_from_deltas
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.trade_tick import BarDelta

BTC = Instrument(
    symbol="BTC_USDT",
    name="비트코인 무기한",
    market=Market.GATE,
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
T0 = datetime(2026, 8, 22, tzinfo=UTC)


def _series(*values: float | None) -> CvdSeries:
    return CvdSeries(tuple(None if v is None else Decimal(str(v)) for v in values))


class TestUpBreakoutConfirmation:
    def test_a_new_high_in_cvd_confirms(self) -> None:
        """진짜 돌파 — 가격이 올라간 봉에서 CVD 도 구간 최대."""
        assert _series(1, 2, 3, 5).confirmed_upto(3, lookback=3) is True

    def test_absorption_is_refused(self) -> None:
        """🔴 흡수 — 가격은 신고가인데 CVD 는 앞선 봉이 더 높다 (위에서 팔고 있다)."""
        assert _series(1, 9, 3, 5).confirmed_upto(3, lookback=3) is False

    def test_a_hole_gives_no_verdict(self) -> None:
        """⛔ 구멍이 있으면 None — 없는 값으로 확인한 척하지 않는다 (규칙 #8)."""
        assert _series(1, None, 3, 5).confirmed_upto(3, lookback=3) is None

    def test_lookback_only_sees_its_window(self) -> None:
        """창 밖의 더 큰 값은 판정에 안 들어온다 — 돌파의 저항 창과 같은 자를 쓴다."""
        assert _series(99, 1, 2, 3).confirmed_upto(3, lookback=2) is True


class TestDownBreakoutIsMirrored:
    def test_a_new_low_in_cvd_confirms(self) -> None:
        """🪞 하단 이탈 — 매도가 실제로 만든 하락이면 CVD 가 구간 최저."""
        assert _series(5, 3, 2, -1).confirmed_downto(3, lookback=3) is True

    def test_a_bounce_in_cvd_is_refused(self) -> None:
        assert _series(-9, 3, 2, -1).confirmed_downto(3, lookback=3) is False

    def test_a_hole_gives_no_verdict(self) -> None:
        assert _series(5, None, 2, -1).confirmed_downto(3, lookback=3) is None


def test_cvd_accumulates_deltas() -> None:
    """누적은 매수 - 매도의 합이다."""
    rows = [
        BarDelta(
            instrument=BTC,
            timeframe=Timeframe.M15,
            ts=T0 + timedelta(minutes=15 * i),
            buy_volume=Decimal(buy),
            sell_volume=Decimal(sell),
            trades=10,
        )
        for i, (buy, sell) in enumerate([(10, 4), (3, 8), (7, 2)])
    ]
    assert cvd_from_deltas(rows) == Decimal(6) + Decimal(-5) + Decimal(5)
    assert cvd_from_deltas([]) == Decimal(0)


def test_the_session_has_the_method_context_calls() -> None:
    """🔴 2026-08-22 사고 — `context()` 가 부르는 메서드가 없어 판 5개가 멈췄다.

    컨텍스트 조립이 부르는 이름과 세션이 가진 이름은 **같이 움직여야** 한다. 한쪽만
    고치면 API 리로드 순간에 도는 판이 전부 죽는다 (단위 테스트가 잡을 수 있는 종류다).
    """
    import inspect

    from updown.orchestration.walkforward.session import Session

    source = inspect.getsource(Session.context)
    for name in {
        line.split("self.")[1].split("(")[0]
        for line in source.splitlines()
        if "self." in line and "(" in line.split("self.")[-1]
    }:
        if name.startswith("_") and not name.startswith("__"):
            assert hasattr(Session, name), f"context() 가 없는 메서드를 부른다: {name}"
