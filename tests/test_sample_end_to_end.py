"""예시 매매법 하나로 **플랫폼이 끝까지 도는가** — 판정 → 진입 → 목표 → 원장 (T224 ③).

공개 저장소는 비공개 매매법 없이 이 시험만으로 살아 있어야 한다. 여기서 보는 것은 매매법의
성적이 아니라 **경로**다: 선언된 플레이북이 레지스트리의 탐지기를 찾고, 세션이 그 셋업으로
들어가고, 손절선이 원장에 적히고, 봉이 흐르면 나간다.

⚠️ 4h 플레이북의 국면은 **일봉 추세**(`MIN_BARS_FOR_TREND` = 210봉)에서 온다 — 일봉이 없으면
국면이 None 이라 어떤 플레이북도 안 돈다. 그래서 급전에 일봉 250개를 같이 넣는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.detectors.sample_ma_cross import RULE_ID
from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Direction, Outcome

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
DAYS = 250
BARS = DAYS * 6  # 4h 봉 수
FINE_PER_BAR = 48  # 4h = 5m x 48
SEALED_BARS = 55


def _closes() -> list[Decimal]:
    """완만한 상승 위에, 봉인 창 안에서 12봉 급락 → 12봉 급반등(교차) → 계속 상승(목표 도달)."""
    out: list[Decimal] = []
    price = Decimal(100)
    dip = BARS - 45
    for i in range(BARS):
        price += Decimal("0.1")
        bump = Decimal(0)
        if dip <= i < dip + 12:
            bump = Decimal(-3) * (i - dip + 1)
        elif dip + 12 <= i < dip + 24:
            bump = Decimal(-36) + Decimal(4) * (i - dip - 11)
        elif i >= dip + 24:
            bump = Decimal(12) + Decimal(2) * (i - dip - 23)
        out.append(price + bump)
    return out


def _candle(frame: Timeframe, ts: datetime, close: Decimal, prev: Decimal) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=prev,
        high=max(prev, close) + 1,
        low=min(prev, close) - 1,
        close=close,
        volume=Decimal(10),
    )


def _session() -> Session:
    closes = _closes()
    coarse: list[Candle] = []
    fine: list[Candle] = []
    daily: list[Candle] = []
    prev = closes[0]
    for i, close in enumerate(closes):
        coarse.append(_candle(Timeframe.H4, START + timedelta(hours=4 * i), close, prev))
        if i >= BARS - SEALED_BARS - 5:
            for j in range(FINE_PER_BAR):
                fine.append(
                    _candle(
                        Timeframe.M5,
                        START + timedelta(hours=4 * i, minutes=5 * j),
                        close,
                        prev if j == 0 else close,
                    )
                )
        prev = close
    prev = closes[5]
    for day in range(DAYS):
        close = closes[day * 6 + 5]
        daily.append(_candle(Timeframe.D1, START + timedelta(days=day), close, prev))
        prev = close
    book = {item.playbook_id: item for item in load_playbooks()}[RULE_ID]
    seal = Seal(
        start=START + timedelta(hours=4 * (BARS - SEALED_BARS)),
        end=START + timedelta(hours=4 * BARS),
    )
    return Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed({Timeframe.M5: fine, Timeframe.H4: coarse, Timeframe.D1: daily}, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )


def test_the_sample_playbook_walks_end_to_end() -> None:
    session = _session()
    for _ in range(20_000):
        if session.finished:
            break
        session.step()
    records = session.ledger.records
    assert records, f"예시 매매법이 한 번은 들어가야 플랫폼 경로가 검증된다: {session.funnel}"
    first = records[0]
    assert first.direction is Direction.LONG
    assert first.planned_stop < first.entry < first.planned_target, "손절·목표가 기하 순서다"
    assert first.playbook.startswith(RULE_ID)
    assert first.outcome is not Outcome.OPEN, f"목표에 닿아 나갔어야 한다: {first.outcome}"
    assert session.funnel.get(f"entered:{first.playbook}") == 1


class _ShutGate:
    """항상 막는 문 — 세션이 문을 묻고, 막히면 사지 않고 깔때기에 적는지 본다 (T279 P3)."""

    def grant(self, at: datetime, exposure: Decimal) -> tuple[Decimal, str | None]:  # noqa: ARG002
        return Decimal(0), "test"


def test_entry_gate_blocks_without_deleting_the_setup() -> None:
    session = _session()
    session.entry_gate = _ShutGate()
    for _ in range(20_000):
        if session.finished:
            break
        session.step()
    assert not session.ledger.records, "문이 막으면 한 건도 안 산다"
    assert session.gate_held >= 1
    assert session.funnel.get("gate:test", 0) == session.gate_held, (
        "막힌 자리는 깔때기에 남는다(§1-0s)"
    )
