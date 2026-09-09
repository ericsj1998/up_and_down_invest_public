"""T254 — 주식 판: 거래소 조건부 = 계획 손절 · 시드 = 예산 · 미전송 보유 기록은 감사로.

① 청산이 없는 시장(`has_liquidation` 거짓)은 `guard_price` 가 보호 손절이 아니라 계획 손절을 준다.
② `seed_split` — 배율 없는 시장은 (예산, 0), 코인은 (계정 총액, 총액 - 예산).
③ 되살아난 보유 기록인데 체결 조각도 포지션도 없으면 `_protect` 가 재전송 대신 `unsent_open` 에
   적고, 감사 항목이 그것을 사람에게 말한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from updown.analysis.playbook.types import Family, Playbook
from updown.apps.api.walkforward import seed_split
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Funding,
    Outcome,
    TradeRecord,
)
from updown.orchestration.walkforward.live_runner import LiveRunner, unsent_open_findings

NVDA = Instrument(Market.NASDAQ, "NVDA", "엔비디아", AssetType.STOCK, Currency.USD)
START = datetime(2026, 9, 1, tzinfo=UTC)


class TestSeedSplit:
    def test_coin_keeps_the_account_as_truth(self) -> None:
        assert seed_split(True, wallet=Decimal(1000), margin=Decimal(300)) == (
            Decimal(1000),
            Decimal(700),
        )
        # 예산이 총액보다 크면 지갑 시작은 0 에서 멈춘다(음수 지갑 없음).
        assert seed_split(True, wallet=Decimal(100), margin=Decimal(300))[1] == Decimal(0)

    def test_stock_owns_only_its_budget(self) -> None:
        # 페이퍼 계좌 5,000 을 두 판이 나눠 써도 각자 300 만 자기 것으로 센다
        # → 합 600 < 5,000 → wallet_drift 없음.
        assert seed_split(False, wallet=Decimal(5000), margin=Decimal(300)) == (
            Decimal(300),
            Decimal(0),
        )


def _bar(i: int) -> Candle:
    return Candle(
        instrument=NVDA,
        timeframe=Timeframe.H1,
        ts=START.replace(hour=i % 24, day=1 + i // 24),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )


def _session() -> Session:
    """탐지 없는 세션 — 대표 플레이북의 `stop_mode` 기본이 close 라 보호 손절 경로를 탄다."""
    bars = [_bar(i) for i in range(48)]
    seal = Seal(start=bars[24].ts, end=bars[-1].ts)
    book = Playbook(
        playbook_id="잠금용",
        version="0",
        market_groups=(),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
    )
    return Session(
        instrument=NVDA,
        playbooks=(book,),
        feed=cast("SealedFeed", SealedFeed({Timeframe.H1: bars}, seal)),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )


def _record(**over: object) -> TradeRecord:
    base: dict[str, object] = {
        "trade_id": "t-nvda",
        "playbook": "잠금용@0",
        "actor": Actor.SYSTEM,
        "direction": Direction.LONG,
        "placed_at": START,
        "entry": Decimal(224),
        "planned_stop": Decimal(217),
        "planned_first": Decimal(230),
        "planned_target": Decimal(234),
        "outcome": Outcome.OPEN,
        "cost_pct": Decimal("0.001"),
        "leverage": Decimal(1),
    }
    return TradeRecord(**{**base, **over})  # type: ignore[arg-type]


class TestGuardPrice:
    def test_no_liquidation_market_arms_the_planned_stop(self) -> None:
        session = _session()
        session.stop_protect_ratio = Decimal("0.5")
        assert session.stop_mode_of(_record()) == "close"
        held = _record()
        # 청산이 있다고 치면(코인) 보호 손절은 계획 손절보다 훨씬 아래다 — NVDA 224 → 68 이 그 자리.
        session.has_liquidation = True
        assert session.guard_price(held) < held.planned_stop
        # 주식: 계획 손절 그대로.
        session.has_liquidation = False
        assert session.guard_price(held) == held.planned_stop


class _Exchange:
    """포지션이 없는 거래소 — 주문이 나가기 전에 재시작된 상황."""

    def __init__(self) -> None:
        self.stops: list[Decimal] = []

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        del instrument
        return {}

    async def stops_for(self, instrument: Instrument, trigger: Decimal, *, long: bool) -> None:
        del instrument, long
        self.stops.append(trigger)


class _Book:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.instrument = NVDA


def _runner(record: TradeRecord, orders: object, *, revived: bool) -> LiveRunner:
    """`__new__` 로 만든 러너 — 재는 것은 `_protect` 가 어느 길로 가는가뿐이다.

    (test_live_incident_20260820 과 같은 이유 — 진짜 생성자는 웹소켓·어댑터를 요구한다.)
    """
    ledger = Ledger(
        seed_cash=Decimal(300),
        funding=Funding.WALLET,
        wallet_start=Decimal(0),
        margin_budget=Decimal(300),
    )
    ledger.records.append(record)
    made = LiveRunner.__new__(LiveRunner)
    made._session = _Book(ledger)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made._orders = orders  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made.failures = 0
    made.last_error = ""
    made.placed = {}
    revived_ids = frozenset({record.trade_id}) if revived else frozenset()
    made._revived_open = revived_ids  # pyright: ignore[reportPrivateUsage]
    return made


class TestUnsentOpen:
    @pytest.mark.asyncio
    async def test_revived_record_without_fills_or_position_is_reported_not_resent(self) -> None:
        record = _record()
        exchange = _Exchange()
        made = _runner(record, exchange, revived=True)
        await made._protect(record)  # pyright: ignore[reportPrivateUsage]
        assert made._unsent_open == frozenset({"t-nvda"})  # pyright: ignore[reportPrivateUsage]
        assert exchange.stops == [] and made.failures == 1
        assert "unsent_open" in made.last_error

    @pytest.mark.asyncio
    async def test_filled_record_without_position_keeps_the_old_message(self) -> None:
        record = _record(entry_fills=((Decimal(224), Decimal(1)),))
        made = _runner(record, _Exchange(), revived=True)
        await made._protect(record)  # pyright: ignore[reportPrivateUsage]
        assert made._unsent_open == frozenset()  # pyright: ignore[reportPrivateUsage]
        assert "포지션이 없다" in made.last_error

    def test_findings_shape(self) -> None:
        got = unsent_open_findings({"b" * 12, "a" * 12})
        assert [item["code"] for item in got] == ["unsent_open", "unsent_open"]
        assert got[0]["detail"].startswith("aaaaaaaa ") and got[0]["level"] == "error"
        assert unsent_open_findings(()) == []
