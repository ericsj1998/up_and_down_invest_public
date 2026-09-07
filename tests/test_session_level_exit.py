"""T42 ③④ · T44 — 세션의 청산이 **매매를 낸 플레이북**의 규칙을 쓰고, 레벨로 나오고,
체결 유형대로 비용을 다시 세는가.

셋 다 "켜지 않으면 한 비트도 안 달라진다"가 절반이다 — 동결 버전(§5.6.2)을 같은 봉에서
나란히 돌려 그것을 잠근다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.playbook.types import Family, Playbook
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
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
from updown.orchestration.walkforward.session import frame_span

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
LEVEL = Decimal(520)
LOSING_AT = START + timedelta(hours=22)
"""이 시각의 15m 봉 하나만 몸통 중심이 레벨 아래다 — 앞뒤는 전부 레벨 위의 양봉."""


def _candle(frame: Timeframe, ts: datetime) -> Candle:
    """레벨 위 양봉 — `LOSING_AT` 의 15분 동안만 레벨 아래 음봉."""
    losing = LOSING_AT <= ts < LOSING_AT + timedelta(minutes=15)
    open_, close = (Decimal(518), Decimal(512)) if losing else (Decimal(525), Decimal(535))
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=open_,
        high=max(open_, close) + 3,
        low=min(open_, close) - 3,
        close=close,
        volume=Decimal(10),
    )


def _book(name: str, *, hold_level: bool = False) -> Playbook:
    """탐지는 안 도는(국면 없음) 플레이북 — 청산 플래그만 다르다."""
    return Playbook(
        playbook_id=name,
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
        hold_level=hold_level,
    )


def _session(*books: Playbook) -> Session:
    """26시간 봉 · 20~24시 봉인 — 보유 기록은 시험이 직접 넣는다."""
    minutes = 60 * 26
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(minutes // 5)
        ],
        Timeframe.M15: [
            _candle(Timeframe.M15, START + timedelta(minutes=15 * i)) for i in range(minutes // 15)
        ],
    }
    seal = Seal(start=START + timedelta(hours=20), end=START + timedelta(hours=24))
    return Session(
        instrument=BTC,
        playbooks=books,
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )


def _hold(session: Session, owner: str, *, level: Decimal | None = LEVEL) -> TradeRecord:
    """돌파 롱 하나를 보유 중으로 심는다 — 손절·목표는 닿지 않을 만큼 멀다."""
    record = TradeRecord(
        trade_id="t-level",
        playbook=owner,
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=START + timedelta(hours=20),
        opened_at=START + timedelta(hours=20),
        entry=Decimal(530),
        planned_stop=Decimal(487),
        planned_target=Decimal(900),
        planned_first=Decimal(900),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
        hold_level=level,
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return record


def _run(session: Session) -> list[TradeRecord]:
    """끝까지 걸어가고 닫힌 기록을 돌려준다."""
    for _ in range(10_000):
        if session.finished:
            break
        session.step()
    return [item for item in session.ledger.records if item.closed_at is not None]


def test_level_exit_fires_when_the_body_loses_the_broken_edge() -> None:
    """T44 B — 레벨을 든 매매는 몸통 중심이 레벨을 잃는 봉에서 **레벨 이탈**로 나온다."""
    book = _book("level", hold_level=True)
    session = _session(book)
    _hold(session, book.attribution)
    closed = _run(session)
    assert [item.outcome for item in closed] == [Outcome.LEVEL_EXIT]
    done = closed[0]
    # 청산가는 계획가가 아니라 **확인 봉의 종가**다 — 손절과 같은 규칙 (§1-0s).
    assert done.exit_price == Decimal(512)
    # 잃은 봉보다 앞에서 나오지 않는다 — 그 전 봉들은 전부 레벨 위의 양봉이다.
    assert done.closed_at is not None and done.closed_at >= LOSING_AT


def test_level_exit_counts_as_money() -> None:
    """🔴 새 결과값이 원장 집계에서 빠지면 잔고가 거짓말한다 (2026-08-22 실측: -8% 가 +2% 로).

    `Ledger.closed` 가 결과값을 열거하므로, 청산이 만들 수 있는 **모든** 결과값이 거기
    있어야 한다 — 한 번 더 빠지면 여기서 잡힌다.
    """
    book = _book("level", hold_level=True)
    session = _session(book)
    _hold(session, book.attribution)
    done = _run(session)[0]
    assert done.outcome is Outcome.LEVEL_EXIT
    assert done in session.ledger.closed, "레벨 이탈이 '확정된 매매' 목록에 없다 — 돈에서 빠진다"
    assert session.ledger.return_pct < 0, "손실로 끝난 매매가 잔고에 반영돼야 한다"
    settled = {
        item.outcome
        for item in session.ledger.records
        if item.outcome not in (Outcome.PENDING, Outcome.OPEN, Outcome.CANCELLED)
    }
    assert settled <= {item.outcome for item in session.ledger.closed}


def test_the_frozen_playbook_does_not_exit_on_the_same_bar() -> None:
    """⛔ 플래그가 꺼진 동결 버전은 같은 봉에서 **아무 일도 안 한다** — 양봉뿐이라 전환
    신호도 없고, 손절·목표도 안 닿는다."""
    book = _book("frozen")
    session = _session(book)
    _hold(session, book.attribution)
    assert _run(session) == []


def test_a_trade_without_a_level_is_not_a_level_trade() -> None:
    """박스 왕복(레벨 없음)은 플래그가 켜져 있어도 레벨 청산의 대상이 아니다."""
    book = _book("level", hold_level=True)
    session = _session(book)
    _hold(session, book.attribution, level=None)
    assert _run(session) == []


def test_the_owner_playbook_decides_the_exit_not_the_first_one() -> None:
    """T42 ③ — 세트에서 둘째 플레이북이 낸 매매는 둘째의 규칙으로 닫힌다."""
    first, second = _book("box"), _book("ride", hold_level=True)
    session = _session(first, second)
    _hold(session, second.attribution)
    assert session._book_of(session.ledger.records[0]) is second  # pyright: ignore[reportPrivateUsage]
    assert [item.outcome for item in _run(session)] == [Outcome.LEVEL_EXIT]


def test_unknown_attribution_falls_back_to_the_representative() -> None:
    """옛 기록·사람 매매는 대표 플레이북으로 닫는다 — 터지지 않는다."""
    first, second = _book("box"), _book("ride", hold_level=True)
    session = _session(first, second)
    record = _hold(session, "legacy@0.1")
    assert session._book_of(record) is first  # pyright: ignore[reportPrivateUsage]


def test_fill_cost_recounts_the_round_trip_by_fill_type() -> None:
    """T42 ④ — 켜면 청산 유형대로, 꺼지면 진입 때 적은 값 그대로."""
    gate = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE)
    book = _book("level", hold_level=True)

    frozen = _session(book)
    _hold(frozen, book.attribution)
    assert _run(frozen)[0].cost_pct == Decimal("0.0015")

    counted = _session(book)
    counted.fill_cost = True
    _hold(counted, book.attribution)
    done = _run(counted)[0]
    # 시장가 진입(다리 없음) + 시장가 청산(레벨 이탈) = 테이커 둘.
    assert done.cost_pct == gate.round_trip_by(entry_is_maker=False, exit_is_maker=False)
    assert done.cost_pct != Decimal("0.0015")


def test_frame_span_reads_the_unit_off_the_value() -> None:
    """리테스트 다리의 수명 계산이 쓰는 봉 길이."""
    assert frame_span(Timeframe.M15) == timedelta(minutes=15)
    assert frame_span(Timeframe.S10) == timedelta(seconds=10)
    assert frame_span(Timeframe.H1) == timedelta(hours=1)
    assert frame_span(Timeframe.D1) == timedelta(days=1)
