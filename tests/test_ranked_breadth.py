"""T1045 — 순위 폭 문(`entry_ranked_breadth`): 순수 식 · 선언 · 작업 · 세션 보류 (플랫폼 장치).

값은 장치를 재는 중립 값이다(실계좌 선언 값은 비공개 시험이 본다).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.indicators.breadth import (
    MarketValues,
    close_above_ma,
    hourly_day_turnover,
    ranked_ma_breadth,
    top_by_turnover,
    window_turnover,
)
from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError
from updown.analysis.playbook.types import Family, Playbook, RankedBreadthGate
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.orchestration.rebalancer.breadth_job import (
    BreadthParams,
    RankedJobConfig,
    ranked_params,
    ranked_rules_of,
    run_ranked_day,
)
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Direction
from updown.orchestration.walkforward.ref_inputs import market_day_of, ranked_days

D = date(2026, 3, 10)
RULE = RankedBreadthGate(
    source="BINANCE",
    top=2,
    min_age_days=3,
    rank_days=2,
    ma_bars=3,
    below=Decimal("0.5"),
    exclude=("XX",),
)


class TestPureFormulas:
    def test_turnover_is_close_times_volume_by_bar_start_day(self) -> None:
        t = datetime(2026, 3, 9, 23, tzinfo=UTC)
        got = hourly_day_turnover(
            [(t, Decimal(2), Decimal(3)), (t + timedelta(hours=1), Decimal(5), Decimal(1))]
        )
        assert got == {date(2026, 3, 9): Decimal(6), date(2026, 3, 10): Decimal(5)}

    def test_window_excludes_the_day_itself(self) -> None:
        by_day = {
            D - timedelta(days=2): Decimal(1),
            D - timedelta(days=1): Decimal(2),
            D: Decimal(9),
        }
        assert window_turnover(by_day, D, 2) == Decimal(3)
        assert window_turnover(by_day, D, 1) == Decimal(2)
        with pytest.raises(ValueError):
            window_turnover(by_day, D, 0)

    def test_top_needs_positive_turnover_known_listing_and_strict_age(self) -> None:
        window = {
            "A": Decimal(5),
            "B": Decimal(9),
            "C": Decimal(7),
            "Z": Decimal(0),
            "U": Decimal(99),
        }
        listed = {
            "A": D - timedelta(days=10),
            "B": D - timedelta(days=3),  # 정확히 3 일 — 넘지 않아 빠진다
            "C": D - timedelta(days=4),
            "Z": D - timedelta(days=10),
        }  # U 는 상장 날을 모른다 — 빠진다
        assert top_by_turnover(window, listed, D, top=2, min_age_days=3) == ("C", "A")

    def test_ties_break_by_name(self) -> None:
        window = {"B": Decimal(5), "A": Decimal(5)}
        listed = {"A": D - timedelta(days=9), "B": D - timedelta(days=9)}
        assert top_by_turnover(window, listed, D, top=1, min_age_days=3) == ("A",)

    def test_close_above_ma_needs_one_more_bar_and_is_strict(self) -> None:
        assert close_above_ma([Decimal(1), Decimal(2), Decimal(3)], 3) is None
        assert close_above_ma([Decimal(9), Decimal(1), Decimal(2), Decimal(6)], 3) is True
        # 마지막 = 평균이면 위가 아니다
        assert close_above_ma([Decimal(9), Decimal(3), Decimal(3), Decimal(3)], 3) is False

    def test_breadth_counts_only_known_members(self) -> None:
        got = ranked_ma_breadth(("A", "B", "C"), {"A": True, "B": None, "C": False})
        assert got.value == Decimal("0.5") and got.counted == 2 and got.members == 3
        assert ranked_ma_breadth(("A",), {}).value is None


class TestDeclaration:
    def test_rule_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            RankedBreadthGate("BINANCE", 0, 3, 2, 3, Decimal("0.5"))
        with pytest.raises(ValueError):
            RankedBreadthGate("BINANCE", 2, 3, 2, 3, Decimal(1))
        with pytest.raises(ValueError):
            RankedBreadthGate("", 2, 3, 2, 3, Decimal("0.5"))

    def test_blocks_is_strictly_below(self) -> None:
        assert RULE.blocks(Decimal("0.4999")) and not RULE.blocks(Decimal("0.5"))

    def test_table_key_holds_the_whole_definition(self) -> None:
        other = RankedBreadthGate("BINANCE", 2, 3, 2, 3, Decimal("0.5"), exclude=("YY",))
        assert RULE.table_market != other.table_market
        assert RULE.table_market.startswith("BINANCE:rank:top2:age3:d2:h3:")
        assert (RULE.breadth_ma, RULE.lag_days, RULE.lag_rank_days) == (3, 0, 2)
        assert ranked_params(RULE) == BreadthParams(3, 0, 2)

    def test_parser(self) -> None:
        body = {
            "source": "BINANCE",
            "top": 2,
            "min_age_days": 3,
            "rank_days": 2,
            "ma_bars": 3,
            "below": "0.5",
            "exclude": ["XX"],
        }
        assert select.ranked_breadth(body, "x") == RULE
        with pytest.raises(PlaybookConfigError, match="모르는 칸"):
            select.ranked_breadth({**body, "topp": 3}, "x")
        with pytest.raises(PlaybookConfigError, match=r"x\.entry_ranked_breadth"):
            select.ranked_breadth({**body, "below": "2"}, "x")


# ── 작업(run_ranked_day) ──────────────────────────────────────────────────────────────────


class _Store:
    """`MarketCloseStore` 의 순위 폭 쪽 메모리 대역."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, date, BreadthParams], MarketValues] = {}

    async def breadth(self, market: str, day: date, params: BreadthParams) -> MarketValues | None:
        return self.rows.get((market, day, params))

    async def add_breadth(
        self, market: str, day: date, params: BreadthParams, values: MarketValues
    ) -> None:
        self.rows.setdefault((market, day, params), values)

    async def days_with_closes(self, market: str, since: date, min_symbols: int) -> set[date]:
        raise AssertionError(f"순위 폭 작업은 종가 표를 안 쓴다({market} {since} {min_symbols})")

    async def add_closes(
        self, market: str, rows: Sequence[tuple[str, date, Decimal]], source: str
    ) -> None:
        raise AssertionError(f"순위 폭 작업은 종가 표를 안 쓴다({market} {len(rows)} {source})")

    async def table(self, market: str, days: Sequence[date]) -> dict[str, list[Decimal | None]]:
        raise AssertionError(f"순위 폭 작업은 종가 표를 안 쓴다({market} {len(days)})")


class _Source:
    """종목마다 같은 꼴의 1H 봉 — 종가 열을 손으로 정한다."""

    def __init__(self, closes: dict[str, list[str]], *, fail: str | None = None) -> None:
        self.closes = closes
        self.fail = fail
        self.calls = 0

    async def listed_symbols(self) -> dict[str, date | None]:
        listed: dict[str, date | None] = {name: date(2026, 1, 1) for name in self.closes}
        listed["XXUSDT"] = date(2026, 1, 1)  # 제외 접두 — 받지도 않는다
        return listed

    async def hourly_rows(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, Decimal, Decimal]]:
        assert symbol != "XXUSDT"
        self.calls += 1
        if symbol == self.fail:
            raise RuntimeError("조회 실패")
        series = self.closes[symbol]
        out: list[tuple[datetime, Decimal, Decimal]] = []
        t = end - timedelta(hours=len(series))
        for close in series:
            if start <= t < end:
                out.append((t, Decimal(close), Decimal(1)))
            t += timedelta(hours=1)
        return out


NOW = datetime(2026, 3, 10, 0, 5, tzinfo=UTC)


def _hours(last: str, n: int = 72, base: str = "10") -> list[str]:
    return [base] * (n - 1) + [last]


class TestRankedJob:
    def test_writes_today_and_yesterday_rows_with_the_same_functions(self) -> None:
        # 거래대금(종가 x 1) — A 가 가장 크고(값 20) · B 다음 · C 작다 → 위 2 = A · B
        src = _Source(
            {
                "AUSDT": _hours("30", base="20"),  # 마지막 종가 30 > 이평 → 위
                "BUSDT": _hours("1", base="15"),  # 마지막 종가 1 < 이평 → 아래
                "CUSDT": _hours("99", base="1"),
            }
        )
        store = _Store()
        got = asyncio.run(
            run_ranked_day(store, src, (RULE,), now=NOW, cfg=RankedJobConfig(days_back=1))
        )
        assert got.action == "computed" and got.failed == 0
        today = store.rows[(RULE.table_market, date(2026, 3, 9), ranked_params(RULE))]
        assert today.breadth == Decimal("0.5") and today.lag_count == 2 and today.lag_ret is None
        # 어제 줄(값의 날 3/9)은 3/9 0 시 앞 봉까지 — 마지막 봉(3/9 23 시)이 안 든다 →
        # 둘 다 평평(종가 = 이평) = 위가 아니다
        before = store.rows[(RULE.table_market, date(2026, 3, 8), ranked_params(RULE))]
        assert before.breadth == Decimal(0)

    def test_existing_rows_ask_nothing(self) -> None:
        src = _Source({"AUSDT": _hours("30")})
        store = _Store()
        asyncio.run(run_ranked_day(store, src, (RULE,), now=NOW, cfg=RankedJobConfig()))
        calls = src.calls
        again = asyncio.run(run_ranked_day(store, src, (RULE,), now=NOW, cfg=RankedJobConfig()))
        assert again.action == "exists" and src.calls == calls

    def test_one_failed_symbol_writes_nothing(self) -> None:
        src = _Source({"AUSDT": _hours("30"), "BUSDT": _hours("1")}, fail="BUSDT")
        store = _Store()
        got = asyncio.run(run_ranked_day(store, src, (RULE,), now=NOW, cfg=RankedJobConfig()))
        assert got.action == "skipped" and got.failed == 1 and not store.rows

    def test_right_after_midnight_waits(self) -> None:
        src = _Source({"AUSDT": _hours("30")})
        got = asyncio.run(
            run_ranked_day(
                _Store(),
                src,
                (RULE,),
                now=datetime(2026, 3, 10, 0, 0, 10, tzinfo=UTC),
                cfg=RankedJobConfig(),
            )
        )
        assert got.action == "skipped" and src.calls == 0

    def test_rules_of_dedupes_by_key(self) -> None:
        books = [_book(RULE), _book(RULE, "other"), _book(None, "none")]
        assert ranked_rules_of(books) == (RULE,)


# ── 세션 보류 ────────────────────────────────────────────────────────────────────────────
COIN = Instrument(Market.GATE, "AAA_USDT", "시험 코인", AssetType.COIN, Currency.USD)


def _book(rule: RankedBreadthGate | None, pid: str = "ranked") -> Playbook:
    return Playbook(
        playbook_id=pid,
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H4,
        regimes=(),
        primary_family=Family.MOMENTUM,
        setups=(),
        entry_ranked_breadth=rule,
    )


def _session_at(cursor: datetime) -> Session:
    """4H 봉만 든 세션 — 커서 앞에 닫힌 4H 봉이 판정 봉이다."""
    first = cursor - timedelta(hours=4 * 30)
    rows = [
        Candle(
            instrument=COIN,
            timeframe=Timeframe.H4,
            ts=first + timedelta(hours=4 * k),
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
            volume=Decimal(1),
        )
        for k in range(31)  # 마지막 봉 = 커서에 시작(봉인 끝까지 자료가 있어야 한다)
    ]
    session = Session(
        instrument=COIN,
        playbooks=(_book(RULE),),
        feed=SealedFeed({Timeframe.H4: rows}, Seal(start=cursor, end=cursor + timedelta(hours=4))),
        ledger=Ledger(seed_cash=Decimal(1_000)),
    )
    session.step_frame = Timeframe.H4
    return session


class TestSessionHold:
    def test_low_value_holds_and_threshold_value_passes(self) -> None:
        s = _session_at(datetime(2026, 3, 10, 8, tzinfo=UTC))  # 판정 봉 = 3/10 04 시 시작
        book = s.playbooks[0]
        s.ranked_breadth = {date(2026, 3, 9): Decimal("0.4999")}
        assert s.entry_hold(book, Direction.SHORT) == "rank_breadth"
        s.ranked_breadth = {date(2026, 3, 9): Decimal("0.5")}
        assert s.entry_hold(book, Direction.SHORT) is None

    def test_unknown_value_holds(self) -> None:
        s = _session_at(datetime(2026, 3, 10, 8, tzinfo=UTC))
        s.ranked_breadth = {}
        assert s.entry_hold(s.playbooks[0], Direction.SHORT) == "rank_breadth"

    def test_midnight_entry_reads_the_day_of_the_decision_bar_start(self) -> None:
        # 0 시 마감 판정 봉 = 전날 20 시 시작 → 전날의 값(표 줄 = 그 전날) ·
        # 그날 줄은 아직 없어도 된다
        s = _session_at(datetime(2026, 3, 10, 0, tzinfo=UTC))
        s.ranked_breadth = {date(2026, 3, 8): Decimal("0.9")}
        assert s.ranked_breadth_now(s.playbooks[0]) == Decimal("0.9")
        assert s.entry_hold(s.playbooks[0], Direction.SHORT) is None

    def test_runner_days_cover_the_decision_bar_day(self) -> None:
        now = datetime(2026, 3, 10, 0, 1, tzinfo=UTC)
        assert market_day_of(now) == date(2026, 3, 9)
        assert ranked_days(now) == (date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9))
