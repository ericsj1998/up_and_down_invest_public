"""ADX 약화 청산 (0.7.0 · T62 갭 분해 A안) — 유지 문턱 이하로 마감하면 전량 나간다.

막아야 하는 실패:
1. 🔴 adx_exit 가 켜졌는데 ADX 가 문턱 아래로 죽어도 안 나가면 — 격자가 검증한
   청산(+1912% vs +313%)이 라이브에 존재하지 않는 것이다 (T62 갭의 재발).
2. 🔴 None(동결)인데 나가면 — 기존 플레이북(0.6.1 이하)의 표본이 오염된다 (§5.6.2).
3. 🔴 손절·목표가 아니라 **SIGNAL_EXIT** 로 적혀야 한다 — 통계가 청산 사유로 갈린다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.playbook.types import Family, Playbook
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
from updown.orchestration.walkforward.session import funding_blocks

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)


def _closes() -> list[Decimal]:
    """상승 40봉(ADX≈100) → 진동 60봉(ADX 가 서서히 죽는다 — 83번째 봉에 31 이하).

    ⚠️ 진동은 **2봉 상승 + 2봉 하락** 반복이다 — 두 가지를 동시에 피해야 한다:
    - 한 봉 걸러 교대(+/-)는 open=직전종가 캔들에서 저점이 안 깎여 -DM 이 0 에
      붙는다 → DX 가 100 에 고정돼 ADX 가 영원히 안 죽는다 (실측).
    - 3연속 같은 색이면 `_turning`(전환 익절)이 끼어들어 기준선(동결) 대조가 오염된다.
    비대칭(+4.0/-3.9)은 반대 장악형 방지다 — 음봉 몸통이 직전 양봉 몸통을 못 감싼다.
    """
    out: list[Decimal] = []
    c = Decimal(100)
    for _ in range(40):
        c += Decimal(5)
        out.append(c)
    steps = (Decimal("4.0"), Decimal("4.0"), Decimal("-3.9"), Decimal("-3.9"))
    for i in range(60):
        c += steps[i % 4]
        out.append(c)
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


def _session(*, adx_exit: int | None) -> Session:
    closes = _closes()
    rows: list[Candle] = []
    prev = closes[0]
    for i, close in enumerate(closes):
        rows.append(_candle(Timeframe.M15, START + timedelta(minutes=15 * i), close, prev))
        prev = close
    # 5m 걸음 축 — 같은 궤적을 잘게 깐다 (내용은 판정에 안 쓰인다).
    fine: list[Candle] = []
    prev = closes[0]
    for i in range(len(closes) * 3):
        close = closes[i // 3]
        fine.append(_candle(Timeframe.M5, START + timedelta(minutes=5 * i), close, prev))
        prev = close
    book = Playbook(
        playbook_id="ma",
        version="0.7.0",
        market_groups=(MarketGroup.COIN,),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
        adx_exit_long=adx_exit,
    )
    # 진동 국면 초입(ADX 아직 ~97)에 들어가 ADX 가 죽는 것을 겪게 한다.
    seal = Seal(
        start=START + timedelta(minutes=15 * 46),
        end=START + timedelta(minutes=15 * 100),
    )
    session = Session(
        instrument=BTC,
        playbooks=(book,),
        feed=SealedFeed({Timeframe.M5: fine, Timeframe.M15: rows}, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    record = TradeRecord(
        trade_id="t-long",
        playbook="ma@0.7.0",
        actor=Actor.SYSTEM,
        direction=Direction.LONG,
        placed_at=seal.start,
        opened_at=seal.start,
        entry=Decimal(300),
        planned_stop=Decimal(50),  # 안 닿는다 — 청산 경로를 ADX 만 남긴다
        planned_target=Decimal(100_000),
        planned_first=Decimal(100_000),
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
    return next(item for item in session.ledger.records if item.trade_id == "t-long")


def test_exits_when_adx_dies_below_hold_threshold() -> None:
    done = _walk(_session(adx_exit=31))
    # 🔴 ADX 가 31 이하로 마감한 봉에서 전량 신호 청산이어야 한다.
    assert done.outcome is Outcome.SIGNAL_EXIT, f"ADX 약화 청산이 안 났다: {done.outcome}"
    assert done.half_at is None, "반익 경로가 아니라 전량이어야 한다"


def test_frozen_none_keeps_holding() -> None:
    done = _walk(_session(adx_exit=None))
    # 🔴 동결(None)이면 같은 데이터에서 아무 일도 없어야 한다 (§5.6.2).
    assert done.outcome is Outcome.OPEN, f"동결인데 청산됐다: {done.outcome}"


class TestFundingBlocks:
    """펀딩캡 게이트 (0.8.1) — 극단 요율에 새로 안 산다."""

    def test_long_blocked_when_rate_above_cap(self) -> None:
        assert funding_blocks(Direction.LONG, Decimal("0.0006"), Decimal("0.0005"))
        assert not funding_blocks(Direction.LONG, Decimal("0.0004"), Decimal("0.0005"))

    def test_short_blocked_on_negative_extreme(self) -> None:
        assert funding_blocks(Direction.SHORT, Decimal("-0.0006"), Decimal("0.0005"))
        assert not funding_blocks(Direction.SHORT, Decimal("0.0006"), Decimal("0.0005")), (
            "양수 극단은 숏에겐 수입이다 — 막지 않는다"
        )

    def test_none_means_asleep(self) -> None:
        """⛔ 백테스트(주입 없음)·조회 실패·동결 플레이북에서는 잠잔다 (§5.6.2)."""
        assert not funding_blocks(Direction.LONG, None, Decimal("0.0005"))
        assert not funding_blocks(Direction.LONG, Decimal("0.01"), None)
