"""T1045 — 개장 점검 봉 더 싣기(`open_bar_add`): 순수 식 · 달력 · 선언 · 세션 기록 (플랫폼 장치).

값은 장치를 재는 중립 값이다(실계좌 선언 값은 비공개 시험이 본다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.indicators.open_bar import (
    fold_complete,
    latest_open_bar,
    mean_true_range,
    open_bar_verdict,
)
from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError
from updown.analysis.playbook.types import Family, OpenBarAdd, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.domain.session import SessionConfigError, load_calendar
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

COIN = Instrument(Market.GATE, "AAA_USDT", "시험 코인", AssetType.COIN, Currency.USD)
CAL = load_calendar()
H = timedelta(hours=1)
M5 = timedelta(minutes=5)


def _bar(ts: datetime, o: str, h: str, low: str, c: str, frame: Timeframe = Timeframe.H1) -> Candle:
    return Candle(
        instrument=COIN,
        timeframe=frame,
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(1),
    )


def _fives(hours: list[tuple[datetime, str, str, str, str]]) -> list[Candle]:
    """시간 봉 명세 → 5m 봉 열(각 5m 가 그 시간의 시 · 고 · 저 · 종을 그대로 가진다)."""
    out: list[Candle] = []
    for start, o, h, low, c in hours:
        for k in range(12):
            out.append(_bar(start + M5 * k, o, h, low, c, Timeframe.M5))
    return out


class TestPureFormulas:
    def test_fold_takes_first_open_extremes_last_close_and_sum_volume(self) -> None:
        t0 = datetime(2026, 1, 8, 10, tzinfo=UTC)
        rows = [
            _bar(t0 + M5 * k, str(100 + k), str(101 + k), str(99 + k), str(100 + k), Timeframe.M5)
            for k in range(12)
        ]
        got = fold_complete(rows, base=Timeframe.M5, frame=Timeframe.H1, start=t0, end=t0 + H)
        assert got is not None
        (one,) = got
        assert (one.open, one.high, one.low, one.close) == (
            Decimal(100),
            Decimal(112),
            Decimal(99),
            Decimal(111),
        )
        assert one.volume == Decimal(12) and one.timeframe is Timeframe.H1

    def test_fold_with_a_hole_is_unknown(self) -> None:
        t0 = datetime(2026, 1, 8, 10, tzinfo=UTC)
        rows = [_bar(t0 + M5 * k, "1", "1", "1", "1", Timeframe.M5) for k in range(12) if k != 7]
        assert (
            fold_complete(rows, base=Timeframe.M5, frame=Timeframe.H1, start=t0, end=t0 + H) is None
        )

    def test_fold_rejects_misaligned_spans(self) -> None:
        t0 = datetime(2026, 1, 8, 10, tzinfo=UTC)
        with pytest.raises(ValueError):
            fold_complete([], base=Timeframe.H1, frame=Timeframe.M5, start=t0, end=t0 + H)
        with pytest.raises(ValueError):
            fold_complete([], base=Timeframe.M5, frame=Timeframe.H1, start=t0, end=t0 + M5)

    def test_mean_true_range_is_a_simple_mean_with_previous_close(self) -> None:
        t0 = datetime(2026, 1, 8, tzinfo=UTC)
        rows = [
            _bar(t0, "10", "10", "10", "10"),
            _bar(t0 + H, "10", "12", "9", "11"),  # 범위 3
            _bar(t0 + 2 * H, "11", "11", "11", "11"),  # 범위 0 · 앞 종가 11 → 0
            _bar(t0 + 3 * H, "15", "16", "15", "15"),  # 범위 1 · |16 - 11| = 5 → 5
        ]
        assert mean_true_range(rows, 3) == Decimal(8) / 3
        assert mean_true_range(rows, 4) is None  # 앞 종가용 한 봉이 모자람
        with pytest.raises(ValueError):
            mean_true_range(rows, 0)

    def _hours(self, check_close: str, flat_range: str = "2") -> list[Candle]:
        t0 = datetime(2026, 1, 8, 9, tzinfo=UTC)
        lo = str(100 - int(flat_range) // 2)
        hi = str(100 + int(flat_range) // 2)
        spec = [(t0 + H * k, "100", hi, lo, "100") for k in range(4)]
        spec.append((t0 + 4 * H, "100", "100", check_close, check_close))
        return _fives(spec)

    def test_exactly_at_the_threshold_is_a_hit(self) -> None:
        # 앞 3 봉 참범위 2 · 문턱 1.5 x 2 = 3 · 숏 쪽 움직임 100 - 97 = 3 → 넘음(같으면 넘은 것)
        start = datetime(2026, 1, 8, 13, tzinfo=UTC)
        got = open_bar_verdict(
            self._hours("97"),
            base=Timeframe.M5,
            frame=Timeframe.H1,
            start=start,
            period=3,
            mult=Decimal("1.5"),
            sign=-1,
        )
        assert got.hit is True and got.move == Decimal(3) and got.atr == Decimal(2)
        assert got.bar is not None and got.bar.close == Decimal(97)

    def test_just_below_the_threshold_is_a_miss(self) -> None:
        start = datetime(2026, 1, 8, 13, tzinfo=UTC)
        got = open_bar_verdict(
            self._hours("97.01"),
            base=Timeframe.M5,
            frame=Timeframe.H1,
            start=start,
            period=3,
            mult=Decimal("1.5"),
            sign=-1,
        )
        assert got.hit is False

    def test_long_sign_reads_the_mirror(self) -> None:
        start = datetime(2026, 1, 8, 13, tzinfo=UTC)
        got = open_bar_verdict(
            self._hours("97"),
            base=Timeframe.M5,
            frame=Timeframe.H1,
            start=start,
            period=3,
            mult=Decimal("1.5"),
            sign=1,
        )
        assert got.hit is False and got.move == Decimal(-3)

    def test_flat_history_has_no_atr_and_is_unknown(self) -> None:
        start = datetime(2026, 1, 8, 13, tzinfo=UTC)
        got = open_bar_verdict(
            self._hours("97", flat_range="0"),
            base=Timeframe.M5,
            frame=Timeframe.H1,
            start=start,
            period=3,
            mult=Decimal("1.5"),
            sign=-1,
        )
        assert got.hit is None and got.why == "atr"

    def test_missing_history_is_unknown(self) -> None:
        start = datetime(2026, 1, 8, 13, tzinfo=UTC)
        got = open_bar_verdict(
            self._hours("97")[12:],  # 맨 앞 시간(앞 종가용)이 없다
            base=Timeframe.M5,
            frame=Timeframe.H1,
            start=start,
            period=3,
            mult=Decimal("1.5"),
            sign=-1,
        )
        assert got.hit is None and got.why == "bars"


class TestCalendar:
    def test_daylight_saving_moves_the_check_bar(self) -> None:
        # 2026-03-08 미국 서머타임 시작 — 금 3/6 개장 14:30Z(EST) → 14 시 봉 ·
        # 월 3/9 13:30Z(EDT) → 13 시 봉
        fri = latest_open_bar(CAL, Market.NYSE, Timeframe.H1, datetime(2026, 3, 6, 15, tzinfo=UTC))
        mon = latest_open_bar(CAL, Market.NYSE, Timeframe.H1, datetime(2026, 3, 9, 14, tzinfo=UTC))
        assert fri == datetime(2026, 3, 6, 14, tzinfo=UTC)
        assert mon == datetime(2026, 3, 9, 13, tzinfo=UTC)

    def test_check_bar_must_be_closed(self) -> None:
        # 월 13 시 봉이 아직 안 닫혔으면 그 앞 거래일(금) 점검 봉
        got = latest_open_bar(
            CAL, Market.NYSE, Timeframe.H1, datetime(2026, 3, 9, 13, 55, tzinfo=UTC)
        )
        assert got == datetime(2026, 3, 6, 14, tzinfo=UTC)

    def test_holiday_has_no_check_bar(self) -> None:
        # 2026-07-03(금) 휴장 → 그날 저녁에 물어도 목 7/2 점검 봉
        got = latest_open_bar(CAL, Market.NYSE, Timeframe.H1, datetime(2026, 7, 3, 20, tzinfo=UTC))
        assert got == datetime(2026, 7, 2, 13, tzinfo=UTC)

    def test_outside_the_holiday_table_is_unknown(self) -> None:
        with pytest.raises(SessionConfigError):
            latest_open_bar(CAL, Market.NYSE, Timeframe.H1, datetime(2028, 3, 1, 15, tzinfo=UTC))

    def test_past_years_are_in_the_table(self) -> None:
        # 2025-01-09 국가 애도일 휴장 · 2024-03-29 성금요일
        assert CAL.regular_session(Market.NASDAQ, datetime(2025, 1, 9).date()) is None
        assert CAL.regular_session(Market.NASDAQ, datetime(2024, 3, 29).date()) is None
        opens, _ = CAL.regular_session(Market.NASDAQ, datetime(2022, 3, 14).date()) or (None, None)
        assert opens == datetime(2022, 3, 14, 13, 30, tzinfo=UTC)


class TestDeclaration:
    def test_rule_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            OpenBarAdd(Market.NYSE, Timeframe.H1, 0, Decimal(1), Decimal("0.5"))
        with pytest.raises(ValueError):
            OpenBarAdd(Market.NYSE, Timeframe.H1, 3, Decimal(0), Decimal("0.5"))
        with pytest.raises(ValueError):
            OpenBarAdd(Market.NYSE, Timeframe.H1, 3, Decimal(1), Decimal("1.5"))

    def test_parser_needs_full_ride_and_known_keys(self) -> None:
        body = {
            "market": "NYSE",
            "timeframe": "1h",
            "atr_period": 3,
            "atr_mult": "1",
            "frac": "0.25",
        }
        assert select.open_bar_add(body, "x") == OpenBarAdd(
            Market.NYSE, Timeframe.H1, 3, Decimal(1), Decimal("0.25")
        )
        with pytest.raises(PlaybookConfigError, match="full_ride"):
            select.open_bar_add(body, "x", full_ride=False)
        with pytest.raises(PlaybookConfigError, match="모르는 칸"):
            select.open_bar_add({**body, "atr_kind": "wilder"}, "x")
        with pytest.raises(PlaybookConfigError, match=r"x\.open_bar_add"):
            select.open_bar_add({**body, "market": "MOON"}, "x")


# ── 세션 기록 ────────────────────────────────────────────────────────────────────────────
#
# 2026-01-08(목) · 01-09(금) NYSE 개장 14:30Z(EST) → 점검 봉 14 시. 숏 진입 체결 = 01-08 05:05Z.
RULE = OpenBarAdd(Market.NYSE, Timeframe.H1, 3, Decimal("1.5"), Decimal("0.25"))
START = datetime(2026, 1, 7, tzinfo=UTC)
ENTRY_AT = datetime(2026, 1, 8, 5, tzinfo=UTC)
CHECK1 = datetime(2026, 1, 8, 14, tzinfo=UTC)
CHECK2 = datetime(2026, 1, 9, 14, tzinfo=UTC)


def _book(rule: OpenBarAdd | None) -> Playbook:
    return Playbook(
        playbook_id="openadd",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.MOMENTUM,
        setups=(),
        hold_through_turn=True,
        full_ride=True,
        open_bar_add=rule,
    )


def _session(
    closes: dict[datetime, str],
    rule: OpenBarAdd | None = RULE,
    *,
    opened_at: datetime = ENTRY_AT,
    calendar: bool = True,
    hole: datetime | None = None,
) -> Session:
    """시간마다 시가 = 앞 시간 종가 · 고저 = 시 · 종 ± 1 · 종가 = `closes` 값(없으면 앞 값)."""
    hours: list[tuple[datetime, str, str, str, str]] = []
    prev = Decimal(500)
    for k in range(80):
        t = START + H * k
        close = Decimal(closes.get(t, str(prev)))
        high, low = max(prev, close) + 1, min(prev, close) - 1
        hours.append((t, str(prev), str(high), str(low), str(close)))
        prev = close
    fives = [row for row in _fives(hours) if hole is None or row.ts != hole]
    # 매매법 축(1H)도 든다(세션이 판정 봉으로 읽는다) — 점검 봉은 5m 를 접어 만든다(실계좌와 같다).
    ones = [_bar(t, o, h, low, c) for t, o, h, low, c in hours]
    source = {Timeframe.M5: fives, Timeframe.H1: ones}
    session = Session(
        instrument=COIN,
        playbooks=(_book(rule),),
        feed=SealedFeed(source, Seal(start=ENTRY_AT, end=START + H * 76)),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    session.event_calendar = CAL if calendar else None
    record = TradeRecord(
        trade_id="t-open",
        playbook=_book(rule).attribution,
        actor=Actor.SYSTEM,
        direction=Direction.SHORT,
        placed_at=opened_at,
        opened_at=opened_at,
        entry=Decimal(500),
        planned_stop=Decimal(700),
        planned_target=Decimal(1),
        planned_first=Decimal(1),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return session


def _walk(session: Session) -> TradeRecord:
    for _ in range(10_000):
        if session.finished:
            break
        session.step()
    (record,) = session.ledger.records
    return record


class TestSessionRecord:
    def test_a_hit_records_the_add_at_the_check_bar_close(self) -> None:
        # 앞 시간 참범위 2(고저 ± 1 · 평평) · 문턱 1.5 x 2 = 3 · 14 시 봉 500 → 497(숏 쪽 3) → 넘음
        got = _walk(_session({CHECK1: "497"}))
        assert got.add_at == CHECK1 + H and got.add_price == Decimal(497)
        assert got.add_frac == Decimal("0.25") and got.add_exposure == Decimal("0.25")
        assert got.planned_stop == Decimal(700)  # 손절은 원 매매 그대로(규칙 #4)

    def test_a_miss_then_a_next_day_hit_adds_once_on_the_hit(self) -> None:
        got = _walk(_session({CHECK1: "499", CHECK1 + H: "499", CHECK2: "496"}))
        assert got.add_at == CHECK2 + H and got.add_price == Decimal(496)

    def test_only_the_first_hit_counts(self) -> None:
        got = _walk(_session({CHECK1: "497", CHECK2: "490"}))
        assert got.add_at == CHECK1 + H and got.add_price == Decimal(497)

    def test_not_held_at_the_check_start_is_not_eligible(self) -> None:
        # 13:55 봉에 들어 14:00 체결 = 점검 봉 시작과 같은 시각 → 든 것으로 본다
        held = _walk(_session({CHECK1: "497"}, opened_at=CHECK1 - M5))
        assert held.add_at == CHECK1 + H
        # 14:00 봉에 들어 14:05 체결 → 점검 봉 시작에 아직 없었다 → 그날은 안 더하고 다음 날은 본다
        late = _session({CHECK1: "497"}, opened_at=CHECK1)
        got = _walk(late)
        assert got.add_at is None
        assert late.funnel.get("open_add:not_held") == 1

    def test_unknown_calendar_adds_nothing(self) -> None:
        session = _session({CHECK1: "497"}, calendar=False)
        got = _walk(session)
        assert got.add_at is None
        assert session.funnel.get("open_add:unknown:calendar") == 1

    def test_a_missing_five_minute_bar_is_unknown(self) -> None:
        session = _session({CHECK1: "497"}, hole=CHECK1 - 2 * H + M5 * 3)
        got = _walk(session)
        assert got.add_at is None
        assert session.funnel.get("open_add:unknown:bars") == 1

    def test_frozen_book_writes_nothing(self) -> None:
        got = _walk(_session({CHECK1: "497"}, rule=None))
        assert got.add_at is None and got.add_frac == Decimal(0)
