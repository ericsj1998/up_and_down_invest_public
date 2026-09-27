"""전고점 크기 기울이기(446 · 447차) — 선언 파싱 · 돌파봉 앞 N일 최고가 · 배수 (2026-09-27).

🔴 못 박는 것:
  - 선언이 없으면 배수 1(동결 · §5.6.2)
  - 최고가는 돌파봉 **앞**만 — 닫힌 일봉 N개 + 그날 돌파봉 앞 봉(돌파봉 자신은 안 센다)
  - 일봉이 모자라면 모름 → `off`(연구가 표식 없음으로 센 것과 같다)
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.playbook.select import PlaybookConfigError, new_high_tilt
from updown.analysis.playbook.types import Family, NewHighTilt, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session

SOL = Instrument(Market.GATE, "SOL_USDT", "솔라나 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
DAYS = 140


def _bar(frame: Timeframe, ts: datetime, high: Decimal, close: Decimal | None = None) -> Candle:
    c = close if close is not None else high - 1
    return Candle(
        instrument=SOL,
        timeframe=frame,
        ts=ts,
        open=c,
        high=high,
        low=c - 1,
        close=c,
        volume=Decimal(10),
    )


def _session(
    *, with_daily: bool = True, peak_day: int = 30, peak: Decimal = Decimal(200)
) -> Session:
    """140일 · 평소 고가 100 · `peak_day` 날 하루만 고가 `peak`."""
    daily = [
        _bar(Timeframe.D1, START + timedelta(days=d), peak if d == peak_day else Decimal(100))
        for d in range(DAYS)
    ]
    hourly = [
        _bar(
            Timeframe.H1,
            START + timedelta(hours=h),
            peak if h // 24 == peak_day else Decimal(100),
        )
        for h in range(DAYS * 24)
    ]
    source = {Timeframe.H1: hourly}
    if with_daily:
        source[Timeframe.D1] = daily
    book = dataclasses.replace(
        Playbook(
            playbook_id="nh",
            version="0",
            market_groups=(MarketGroup.COIN,),
            timeframe=Timeframe.H1,
            regimes=(),
            primary_family=Family.TREND,
            setups=(),
        ),
        new_high_tilt=NewHighTilt(days=120, on=Decimal("1.88"), off=Decimal("0.94")),
    )
    end = START + timedelta(days=DAYS - 1)
    session = Session(
        instrument=SOL,
        playbooks=(book,),
        feed=SealedFeed(source, Seal(start=end - timedelta(hours=2), end=end)),
        ledger=Ledger(seed_cash=Decimal(1000)),
    )
    session.step_frame = Timeframe.H1  # 펀드 재현 · 실계좌 돌파 판과 같은 걸음 축
    while not session.finished:
        session.step()
    return session


def _breakout(session: Session, close: Decimal) -> Candle:
    last = session.feed.judged(Timeframe.H1)[-1]
    return _bar(Timeframe.H1, last.ts, close + 1, close)


class TestDeclaration:
    def test_parses_and_rejects(self) -> None:
        got = new_high_tilt({"days": 120, "on": "1.88", "off": "0.94"}, "x")
        assert got == NewHighTilt(days=120, on=Decimal("1.88"), off=Decimal("0.94"))
        with pytest.raises(PlaybookConfigError, match=r"x\.new_high_tilt"):
            new_high_tilt({"days": 0, "on": "1", "off": "1"}, "x")
        with pytest.raises(PlaybookConfigError):
            new_high_tilt({"days": 120, "on": "1.8"}, "x")


class TestPriorHigh:
    def test_peak_inside_window_blocks_the_mark(self) -> None:
        session = _session(peak_day=DAYS - 60)  # 60일 전 고가 200 — 창 안
        book = session.playbooks[0]
        assert session.prior_high(_breakout(session, Decimal(150)), 120) == Decimal(200)
        assert session._new_high_mult(book, _breakout(session, Decimal(150))) == Decimal("0.94")  # pyright: ignore[reportPrivateUsage]
        assert session._new_high_mult(book, _breakout(session, Decimal(201))) == Decimal("1.88")  # pyright: ignore[reportPrivateUsage]

    def test_peak_outside_window_is_forgotten(self) -> None:
        session = _session(peak_day=5)  # 130일쯤 전 — 120일 창 밖
        book = session.playbooks[0]
        assert session.prior_high(_breakout(session, Decimal(150)), 120) == Decimal(100)
        assert session._new_high_mult(book, _breakout(session, Decimal(150))) == Decimal("1.88")  # pyright: ignore[reportPrivateUsage]

    def test_missing_daily_is_unknown_and_off(self) -> None:
        session = _session(with_daily=False)
        book = session.playbooks[0]
        assert session.prior_high(_breakout(session, Decimal(150)), 120) is None
        assert session._new_high_mult(book, _breakout(session, Decimal(150))) == Decimal("0.94")  # pyright: ignore[reportPrivateUsage]
        assert session.funnel.get("new_high:unknown") == 1

    def test_no_declaration_is_frozen(self) -> None:
        session = _session()
        plain = dataclasses.replace(session.playbooks[0], new_high_tilt=None)
        assert session._new_high_mult(plain, _breakout(session, Decimal(500))) == Decimal(1)  # pyright: ignore[reportPrivateUsage]
