"""혼합 3.1 + 일봉 A + G2 · K07 실계좌 배선 (T389 · 2026-10-05).

🔴 못 박는 것:
  - 실계좌 묶음 다리 값이 T376 AG70(K07) 재현과 같다 — 노출 · 브레이크 7% · 돌파 폭 상한 7.2 ·
    legs_revision 4
  - 이평 띠 선언이 재현 래퍼(`t359_fund_replay_m31.TILTS`)와 같다
  - G2 창: 월봉 저항 거부 판정이 연구(`t368_weekly_monthly.level_events`)와 같은 규칙 ·
    다음 날부터 28일
  - 날짜 창 · 낡은 표 · 일봉 첫 1시간 문이 세션에서 그 뜻대로 돈다
  - 선언이 없으면 배수 1 · 문 열림(동결 · §5.6.2)
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from updown.analysis.context.month_reject import level_events, month_bars, reject_windows
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks, size_windows
from updown.analysis.playbook.types import DateWindows, Family, Playbook, SmaTilt
from updown.common.domain.instrument import MarketGroup, Timeframe
from updown.orchestration.walkforward import Session

LEGS = {
    "private_strategy": "4.0",
    "private_strategy": "1.5",
    "private_strategy": "2.25",
    "private_strategy": "1.875",
    "private_strategy": "0.75",
    "private_strategy": "0.375",
}


def _books() -> dict[str, Playbook]:
    return {b.playbook_id: b for b in load_playbooks()}


class TestLiveDeclaration:
    def test_k07_leg_values(self) -> None:
        books = _books()
        for leg, exposure in LEGS.items():
            assert books[leg].leg_exposure == Decimal(exposure), leg
            assert books[leg].version == "0.1.0", f"{leg} 귀속 키 버전은 그대로"
        for leg in set(LEGS) - {"private_strategy"}:
            brake = books[leg].drawdown_brake
            assert brake is not None and brake.at == Decimal("0.10"), leg
        breakout = books["private_strategy"]
        assert breakout.drawdown_brake is not None
        assert breakout.drawdown_brake.scale == Decimal("0.25")
        assert breakout.breadth_cap is not None and breakout.breadth_cap.cap == Decimal("7.2")
        assert books["private_strategy"].legs_revision == 4

    def test_tilts_match_replay_wrapper(self) -> None:
        books = _books()
        d1, h4 = Timeframe.D1, Timeframe.H4
        assert books["private_strategy"].sma_tilts == (
            SmaTilt(d1, 20, Decimal("0.75"), low=Decimal("3.994"), high=Decimal("9.249")),
            SmaTilt(d1, 50, Decimal(0), back=5, low=Decimal("-1.946"), high=Decimal("-0.629")),
        )
        assert books["private_strategy"].sma_tilts == (
            SmaTilt(h4, 20, Decimal(0), low=Decimal("0.66")),
        )
        assert books["private_strategy"].sma_tilts == (
            SmaTilt(h4, 20, Decimal("0.5"), low=Decimal("-3.66")),
        )
        assert books["private_strategy"].sma_tilts == ()

    def test_no_g2_and_daily_a(self) -> None:
        books = _books()
        # G2 는 2026-10-05 밤 뺐다(사용자) — 실계좌 다리 어디에도 날짜 창 선언이 없다
        for leg in LEGS:
            assert books[leg].size_windows is None, leg
        assert books["private_strategy"].fresh_close_hours == 1
        for leg in set(LEGS) - {"private_strategy"}:
            assert books[leg].fresh_close_hours is None, leg


class TestDateWindows:
    RULE = DateWindows(
        mult=Decimal("0.5"),
        windows=((date(2026, 4, 1), date(2026, 4, 28)),),
        evaluated_through=date(2026, 9, 30),
    )

    def test_holds_inclusive(self) -> None:
        assert self.RULE.holds(date(2026, 4, 1))
        assert self.RULE.holds(date(2026, 4, 28))
        assert not self.RULE.holds(date(2026, 4, 29))
        assert not self.RULE.holds(date(2026, 3, 31))

    def test_stale_after_next_month_end(self) -> None:
        assert not self.RULE.stale(date(2026, 10, 31))
        assert self.RULE.stale(date(2026, 11, 1))
        dec = dataclasses.replace(self.RULE, evaluated_through=date(2026, 11, 30))
        assert not dec.stale(date(2026, 12, 31))
        assert dec.stale(date(2027, 1, 1))

    def test_rejects_bad_values(self) -> None:
        with pytest.raises(ValueError, match="배수"):
            DateWindows(Decimal(-1), (), date(2026, 9, 30))
        with pytest.raises(ValueError, match="창 끝"):
            DateWindows(Decimal(1), ((date(2026, 4, 2), date(2026, 4, 1)),), date(2026, 9, 30))

    def test_parser(self) -> None:
        got = size_windows(
            {
                "mult": "0.5",
                "evaluated_through": date(2026, 9, 30),
                "windows": [["2026-04-01", date(2026, 4, 28)]],
            },
            "x",
        )
        assert got == self.RULE
        with pytest.raises(PlaybookConfigError):
            size_windows({"mult": "0.5", "windows": []}, "x")
        with pytest.raises(PlaybookConfigError):
            size_windows(
                {"mult": "0.5", "evaluated_through": "2026-09-30", "windows": [["2026-04-01"]]}, "x"
            )


def _month(
    year: int, month: int, base: float, spike: float | None = None, close: float | None = None
) -> dict[date, float]:
    first = date(year, month, 1)
    nxt = date(year + month // 12, month % 12 + 1, 1)
    days = [first + timedelta(days=j) for j in range((nxt - first).days)]
    out = dict.fromkeys(days, base)
    if spike is not None:
        out[days[10]] = spike
    if close is not None:
        out[days[-1]] = close
    return out


class TestMonthReject:
    def test_reject_month_opens_next_day_window(self) -> None:
        closes: dict[date, float] = {}
        closes |= _month(2023, 1, 100)
        closes |= _month(2023, 2, 100, spike=150)  # 피벗 고점 150
        closes |= _month(2023, 3, 110)
        closes |= _month(2023, 4, 120)
        closes |= _month(2023, 5, 120, spike=160, close=140)  # 150 에 닿고 아래로 마감 = 거부
        windows, through = reject_windows(closes)
        assert windows == ((date(2023, 6, 1), date(2023, 6, 28)),)
        assert through == date(2023, 5, 31)

    def test_open_month_is_dropped(self) -> None:
        closes = _month(2023, 1, 100) | {date(2023, 2, 1): 101.0}
        bars = month_bars(closes)
        assert [b.last for b in bars] == [date(2023, 1, 31)]

    def test_accept_wins_over_reject(self) -> None:
        closes: dict[date, float] = {}
        closes |= _month(2023, 1, 100)
        closes |= _month(2023, 2, 100, spike=150)
        closes |= _month(2023, 3, 110)
        closes |= _month(2023, 4, 155)  # 직전 달이 이미 150 위 마감
        closes |= _month(2023, 5, 152, spike=170, close=152)
        events = level_events(month_bars(closes), 36, 1)
        assert events[-1] == "accept"


class _Fake:
    def __init__(self, cursor: datetime) -> None:
        self.funnel: dict[str, int] = {}
        self.cursor = cursor

    def _count(self, key: str) -> None:
        self.funnel[key] = self.funnel.get(key, 0) + 1


def _book(**kw: Any) -> Playbook:
    return dataclasses.replace(
        Playbook(
            playbook_id="t389",
            version="0",
            market_groups=(MarketGroup.COIN,),
            timeframe=kw.pop("timeframe", Timeframe.D1),
            regimes=(),
            primary_family=Family.TREND,
            setups=(),
        ),
        **kw,
    )


def _bar(ts: datetime, frame: Timeframe = Timeframe.H1) -> Any:
    return SimpleNamespace(ts=ts, timeframe=frame)


class TestSessionGates:
    def test_window_mult_uses_signal_close_date(self) -> None:
        fake = _Fake(datetime(2026, 4, 1, tzinfo=UTC))
        book = _book(size_windows=TestDateWindows.RULE)
        # 3/31 23:00 1H 봉은 4/1 00:00 에 닫힌다 → 창 안
        got = Session._window_mult(fake, book, _bar(datetime(2026, 3, 31, 23, tzinfo=UTC)))  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == Decimal("0.5")
        got = Session._window_mult(fake, book, _bar(datetime(2026, 3, 31, 22, tzinfo=UTC)))  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == Decimal(1)
        assert fake.funnel == {"size_windows:in": 1}

    def test_stale_table_is_one_and_counted(self) -> None:
        fake = _Fake(datetime(2026, 11, 2, tzinfo=UTC))
        book = _book(size_windows=TestDateWindows.RULE)
        got = Session._window_mult(fake, book, _bar(datetime(2026, 11, 2, 5, tzinfo=UTC)))  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == Decimal(1)
        assert fake.funnel == {"size_windows:stale": 1}

    def test_no_declaration_is_frozen(self) -> None:
        fake = _Fake(datetime(2026, 4, 1, 5, tzinfo=UTC))
        book = _book()
        assert Session._window_mult(fake, book, _bar(datetime(2026, 4, 1, tzinfo=UTC))) == 1  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert Session._fresh_close(fake, book)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert fake.funnel == {}

    @pytest.mark.parametrize(
        ("hour", "minute", "ok"),
        [(0, 0, True), (0, 55, True), (1, 0, False), (13, 5, False), (23, 55, False)],
    )
    def test_fresh_close_first_hour_after_daily_close(
        self, hour: int, minute: int, ok: bool
    ) -> None:
        fake = _Fake(datetime(2026, 10, 5, hour, minute, tzinfo=UTC))
        book = _book(fresh_close_hours=1)
        assert Session._fresh_close(fake, book) is ok  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
