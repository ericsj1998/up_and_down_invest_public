"""T318 — 조용한 반등에 더 싣기 판정: 선언 · 파서 · 세션 기록.

494차 `t296_wave286.first_bar` 와 같은 자다. 진입봉 다음 **첫** 판정 봉이 닫힐 때 매매가 열려
있고 · 종가가 진입가의 불리한 쪽(숏이면 위)이고 · 그 봉 거래량 ÷ 진입봉 거래량 < 문턱이면 그
종가 · 마감 시각에 처음 크기의 `frac` 배를 적는다. 불타기(`add_on`)와 한 칸 · 선언이 없으면
한 비트도 안 달라진다(§5.6.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks
from updown.analysis.playbook.types import AddOn, Family, Playbook, QuietAdd
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
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
ENTRY_AT = START + timedelta(hours=20)
ENTRY = Decimal(530)
QUIET = QuietAdd(vol_ratio=Decimal("0.8160341454604354"), frac=Decimal("0.5"))
CONFIRM = AddOn(confirm_pct=Decimal("0.06"), frac=Decimal("0.5"))


def _book(quiet: QuietAdd | None, add_on: AddOn | None = None) -> Playbook:
    """탐지는 안 도는 1H 플레이북 — 캔들 색을 안 보고 들고 간다."""
    return Playbook(
        playbook_id="quiet",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.MOMENTUM,
        setups=(),
        hold_through_turn=True,
        full_ride=True,
        add_on=add_on,
        quiet_add=quiet,
    )


def _session(
    book: Playbook,
    closes: dict[int, int],
    volumes: dict[int, int],
    *,
    direction: Direction = Direction.SHORT,
) -> Session:
    """시각(시간 번호)별 1H 종가 · 거래량 — 20시에 체결(진입봉 = 19시 봉)."""

    def pick(table: dict[int, int], hour: int) -> Decimal:
        return Decimal(table[max(k for k in table if k <= max(hour, 0))])

    def candle(frame: Timeframe, ts: datetime) -> Candle:
        hour = int((ts - START).total_seconds() // 3600)
        open_, close = pick(closes, hour - 1), pick(closes, hour)
        return Candle(
            instrument=BTC,
            timeframe=frame,
            ts=ts,
            open=open_,
            high=max(open_, close) + 1,
            low=min(open_, close) - 1,
            close=close,
            volume=pick(volumes, hour),
        )

    hours = 30
    source = {
        Timeframe.M5: [
            candle(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(hours * 12)
        ],
        Timeframe.H1: [candle(Timeframe.H1, START + timedelta(hours=i)) for i in range(hours)],
    }
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, Seal(start=ENTRY_AT, end=START + timedelta(hours=28))),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    long = direction is Direction.LONG
    record = TradeRecord(
        trade_id="t-quiet",
        playbook=book.attribution,
        actor=Actor.SYSTEM,
        direction=direction,
        placed_at=ENTRY_AT,
        opened_at=ENTRY_AT,
        entry=ENTRY,
        planned_stop=Decimal(400) if long else Decimal(700),
        planned_target=Decimal(5_000) if long else Decimal(1),
        planned_first=Decimal(5_000) if long else Decimal(1),
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


class TestDeclaration:
    def test_rule_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            QuietAdd(vol_ratio=Decimal(0), frac=Decimal("0.5"))
        with pytest.raises(ValueError):
            QuietAdd(vol_ratio=Decimal("0.8"), frac=Decimal("1.5"))

    def test_parser_needs_full_ride_and_both_keys(self) -> None:
        with pytest.raises(PlaybookConfigError, match="full_ride"):
            select.quiet_add({"vol_ratio": "0.8", "frac": "0.5"}, "x", full_ride=False)
        with pytest.raises(PlaybookConfigError, match=r"x\.quiet_add"):
            select.quiet_add({"vol_ratio": "0.8"}, "x")

    def test_only_the_live_macd_short_leg_carries_it(self) -> None:
        """연구 원장 문턱 그대로(앞 창 중앙 · `t318_threshold.py`) · 선언 버전 그대로."""
        books = {item.playbook_id: item for item in load_playbooks()}
        on = {name for name, book in books.items() if book.quiet_add is not None}
        assert on == {"private_strategy"}
        assert books["private_strategy"].quiet_add == QUIET
        assert books["private_strategy"].version == "0.1.0"


class TestShortQuietBounce:
    def test_a_quiet_bounce_is_the_add(self) -> None:
        # 진입봉(19시) 거래량 10 · 첫 봉(20시) 540 > 530 반등 · 거래량 5(비 0.5 < 0.816)
        # → 21시 마감 · 540
        got = _walk(_session(_book(QUIET), {0: 530, 20: 540, 21: 520}, {0: 10, 20: 5}))
        assert got.add_at == START + timedelta(hours=21)
        assert got.add_price == Decimal(540)
        assert got.add_frac == Decimal("0.5") and got.add_exposure == Decimal("0.5")

    def test_a_heavy_bounce_is_not(self) -> None:
        got = _walk(_session(_book(QUIET), {0: 530, 20: 540}, {0: 10, 20: 9}))
        assert got.add_at is None and got.add_frac == Decimal(0)

    def test_a_quiet_drop_is_not_a_bounce(self) -> None:
        got = _walk(_session(_book(QUIET), {0: 530, 20: 520}, {0: 10, 20: 5}))
        assert got.add_at is None

    def test_only_the_first_bar_after_entry_counts(self) -> None:
        # 첫 봉은 내려감 · 둘째 봉이 조용히 반등해도 494차 규칙(진입 1봉)이 아니다
        got = _walk(_session(_book(QUIET), {0: 530, 20: 520, 21: 540}, {0: 10, 20: 9, 21: 5}))
        assert got.add_at is None

    def test_the_threshold_is_strict(self) -> None:
        # 거래량 비가 문턱과 같으면 조용하지 않다(연구 `V1 < med`)
        ratio = QuietAdd(vol_ratio=Decimal("0.5"), frac=Decimal("0.5"))
        got = _walk(_session(_book(ratio), {0: 530, 20: 540}, {0: 10, 20: 5}))
        assert got.add_at is None


class TestOneSlotWithTheConfirmAdd:
    def test_the_quiet_add_comes_first_and_keeps_the_slot(self) -> None:
        # 조용한 반등(21시) 뒤 -6% 확인(470 ≤ 498.2)이 와도 한 매매 한 번 — 조용한 쪽 그대로
        got = _walk(
            _session(_book(QUIET, CONFIRM), {0: 530, 20: 540, 21: 500, 22: 470}, {0: 10, 20: 5})
        )
        assert got.add_at == START + timedelta(hours=21) and got.add_price == Decimal(540)

    def test_without_a_quiet_bounce_the_confirm_add_is_untouched(self) -> None:
        got = _walk(
            _session(_book(QUIET, CONFIRM), {0: 530, 20: 520, 21: 500, 22: 470}, {0: 10, 20: 5})
        )
        assert got.add_at == START + timedelta(hours=23) and got.add_price == Decimal(470)

    def test_frozen_book_writes_nothing(self) -> None:
        got = _walk(_session(_book(None), {0: 530, 20: 540}, {0: 10, 20: 5}))
        assert got.add_at is None and not got.add_broken
