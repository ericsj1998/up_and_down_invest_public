"""펀딩(포지션 유지비)이 원장에 들어간다 (T226 · 사용자 2026-09-07).

막아야 하는 실패:
1. 보유가 길어도 비용이 안 는다 — 정산 경계(00·08·16 UTC)를 지날 때마다 요율만큼 늘어야 한다.
2. 비례식(보유시간/8h) — 7시간 55분을 들어도 경계를 안 지났으면 0, 10분을 들어도 지났으면 한 번.
3. 같은 정산을 두 번 붙인다 — `seen` 이 막는다.
4. 다른 종목의 정산이 붙는다 — Gate 는 `text`, Binance 는 `contract` 로 가른다(밑줄 무시).
5. 라이브에서 모형까지 켜져 두 번 낸다 — 라이브 세션은 `model_funding=False`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.playbook.types import Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.evidence import Family
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.funding import attribute_funding, settlement_boundaries
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Outcome,
    TradeRecord,
)

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 9, 1, tzinfo=UTC)


class TestSettlementBoundaries:
    def test_crossing_one_boundary(self) -> None:
        got = settlement_boundaries(T0 + timedelta(hours=7, minutes=55), T0 + timedelta(hours=8))
        assert got == [T0 + timedelta(hours=8)]

    def test_no_boundary_inside_a_window(self) -> None:
        assert settlement_boundaries(T0 + timedelta(hours=1), T0 + timedelta(hours=7)) == []

    def test_a_day_has_three(self) -> None:
        got = settlement_boundaries(T0 - timedelta(minutes=5), T0 + timedelta(hours=23, minutes=59))
        assert got == [T0, T0 + timedelta(hours=8), T0 + timedelta(hours=16)]

    def test_boundary_at_previous_is_excluded_at_now_is_included(self) -> None:
        """규칙: 진입 < 정산 <= 지금."""
        assert settlement_boundaries(T0, T0 + timedelta(hours=8)) == [T0 + timedelta(hours=8)]


class TestTradeRecordGain:
    def test_funding_reduces_gain_like_cost(self) -> None:
        record = TradeRecord(
            trade_id="t",
            playbook="p",
            actor=Actor.SYSTEM,
            placed_at=T0,
            opened_at=T0,
            entry=Decimal(100),
            exit_price=Decimal(101),
            outcome=Outcome.TAKE_PROFIT,
            closed_at=T0 + timedelta(days=1),
            cost_pct=Decimal("0.001"),
            leverage=Decimal(5),
            funding_pct=Decimal("0.0003"),
        )
        # (1% - 0.1% - 0.03%) x 5
        assert record.gain_pct == Decimal("4.35")


def _rows(*items: tuple[str, str, str, str]) -> list[dict[str, str]]:
    return [
        {"type": kind, "change": change, "time": stamp, "text": text}
        for kind, change, stamp, text in items
    ]


class TestAttributeFunding:
    def test_only_this_symbol_inside_the_open_interval_and_once(self) -> None:
        opened = T0 + timedelta(hours=1)
        t8 = str(int((T0 + timedelta(hours=8)).timestamp()))
        t16 = str(int((T0 + timedelta(hours=16)).timestamp()))
        t0 = str(int(T0.timestamp()))
        rows = _rows(
            ("fund", "-0.10", t8, "NEAR_USDT:1"),
            ("fund", "-0.20", t16, "NEAR_USDT:2"),
            ("fund", "-9.99", t8, "BTC_USDT:3"),  # 다른 종목
            ("fund", "-0.05", t0, "NEAR_USDT:0"),  # 열리기 전
            ("pnl", "1.00", t8, "NEAR_USDT:9"),  # 펀딩이 아니다
        )
        fresh, seen = attribute_funding(
            rows, symbol="NEAR_USDT", opened_at=opened, closed_at=None, seen=set()
        )
        assert [f.change for f in fresh] == [Decimal("-0.10"), Decimal("-0.20")]
        again, _ = attribute_funding(
            rows, symbol="NEAR_USDT", opened_at=opened, closed_at=None, seen=seen
        )
        assert again == [], "같은 정산을 두 번 붙이지 않는다"

    def test_binance_rows_match_by_contract_without_underscore(self) -> None:
        t8 = str(int((T0 + timedelta(hours=8)).timestamp()) * 1000)  # ms
        rows = [{"type": "FUNDING_FEE", "change": "-0.3", "time": t8, "contract": "NEARUSDT"}]
        fresh, _ = attribute_funding(
            rows, symbol="NEAR_USDT", opened_at=T0, closed_at=None, seen=set()
        )
        assert len(fresh) == 1 and fresh[0].at == T0 + timedelta(hours=8)


def _candle(frame: Timeframe, ts: datetime) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(10),
    )


def _session_holding_long(*, model_funding: bool) -> Session:
    """27시간 봉 · 봉인 01:00~25:00 — 롱 하나를 보유 중으로 심는다 (경계 08·16·00 셋을 지난다)."""
    minutes = 60 * 27
    source = {
        Timeframe.M5: [
            _candle(Timeframe.M5, T0 + timedelta(minutes=5 * i)) for i in range(minutes // 5)
        ],
        Timeframe.M15: [
            _candle(Timeframe.M15, T0 + timedelta(minutes=15 * i)) for i in range(minutes // 15)
        ],
    }
    book = Playbook(
        playbook_id="hold",
        version="0.1",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
    )
    seal = Seal(start=T0 + timedelta(hours=1), end=T0 + timedelta(hours=25))
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
        model_funding=model_funding,
    )
    record = TradeRecord(
        trade_id="t-long",
        playbook="hold@0.1",
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=seal.start,
        opened_at=seal.start,
        entry=Decimal(100),
        planned_stop=Decimal(1),  # 안 닿는다
        planned_target=Decimal(100_000),
        planned_first=Decimal(100_000),
        outcome=Outcome.OPEN,
        cost_pct=Decimal("0.0015"),
    )
    session.ledger.add(record)
    session._open = record  # pyright: ignore[reportPrivateUsage]
    return session


def _walk(session: Session) -> None:
    for _ in range(10_000):
        if session.finished:
            break
        session.step()


class TestModelFundingInSealedSessions:
    def test_three_settlements_in_a_day_are_charged(self) -> None:
        session = _session_holding_long(model_funding=True)
        _walk(session)
        held = session.position
        assert held is not None
        # GATE 설정 funding_pct_per_8h = 0.0001 · 롱은 낸다 · 08:00 · 16:00 · 00:00 세 번
        assert held.funding_pct == Decimal("0.0003")
        assert session.funnel.get("funding:settlements", 0) >= 1

    def test_live_sessions_leave_it_to_the_exchange(self) -> None:
        session = _session_holding_long(model_funding=False)
        _walk(session)
        held = session.position
        assert held is not None
        assert held.funding_pct == 0

    def test_keys_accumulate_and_reset_starts_over(self) -> None:
        """열쇠가 매매에 남아야 재시작 뒤 같은 정산을 거른다 (2026-09-08 실측 2.7배)."""
        session = _session_holding_long(model_funding=False)
        session.apply_funding(paid=Decimal("0.1"), pct=Decimal("0.0001"), keys=("1:-0.1",))
        session.apply_funding(paid=Decimal("0.1"), pct=Decimal("0.0001"), keys=("2:-0.1",))
        held = session.position
        assert held is not None
        assert held.funding_paid == Decimal("0.2")
        assert held.funding_keys == ("1:-0.1", "2:-0.1")
        # 옛 기록 바로잡기 — 누적을 버리고 이번 값으로
        session.apply_funding(
            paid=Decimal("0.05"), pct=Decimal("0.00005"), keys=("3:-0.05",), reset=True
        )
        held = session.position
        assert held is not None
        assert held.funding_paid == Decimal("0.05") and held.funding_keys == ("3:-0.05",)

    def test_restored_keys_stop_the_same_settlement_twice(self) -> None:
        """재시작: 메모리 집합은 비었지만 매매에 적힌 열쇠로 시작하면 다시 붙지 않는다."""
        rows = [
            {
                "type": "fund",
                "change": "-0.01",
                "time": str(int(t.timestamp())),
                "text": "NEAR_USDT:1",
            }
            for t in (T0 + timedelta(hours=8), T0 + timedelta(hours=16))
        ]
        fresh, seen = attribute_funding(
            rows, symbol="NEAR_USDT", opened_at=T0, closed_at=None, seen=set()
        )
        assert len(fresh) == 2
        again, _ = attribute_funding(
            rows, symbol="NEAR_USDT", opened_at=T0, closed_at=None, seen=set(seen)
        )
        assert again == []

    def test_apply_funding_updates_the_open_record_and_the_ledger(self) -> None:
        session = _session_holding_long(model_funding=False)
        session.apply_funding(paid=Decimal("0.42"), pct=Decimal("0.0002"))
        held = session.position
        assert held is not None
        assert held.funding_paid == Decimal("0.42") and held.funding_pct == Decimal("0.0002")
        assert session.ledger.find("t-long") is not None
        assert session.ledger.find("t-long").funding_paid == Decimal("0.42")  # type: ignore[union-attr]
