"""연속 재생 — 창 안에서 매매가 **여러 번** 나오는가.

막아야 하는 실패:

1. 🔴 **한 창에 매매가 최대 1건인 것** — 예전 설계가 그랬다. 첫 청산에서 멈췄다
2. 🔴 **계획을 그 봉의 종가로 세우는 것** — 봉이 끝나야 아는 값으로 그 봉의 체결을
   판정하면 미래 참조다
3. 청산 봉에서 다음 매매가 겹쳐 시작하는 것
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from updown.analysis.structures.replay import Event, Trade, replay_all
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
START = datetime(2026, 8, 16, tzinfo=UTC)


def bar(i: int, low: int, high: int) -> Candle:
    """테스트용 봉 — 저가에서 열어 고가로 닫는다."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=START + timedelta(hours=i),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
        volume=Decimal(100),
    )


def oscillation(cycles: int) -> list[Candle]:
    """지지와 저항 사이를 오가는 봉들 — 박스권을 만든다.

    ⚠️ 레벨 원장은 ZigZag 마디로 서므로 **되돌림이 ATR 배수를 넘어야** 레벨이 선다.
    폭을 넉넉히 잡아 마디가 확실히 찍히게 한다.
    """
    rows: list[Candle] = []
    index = 0
    for _ in range(cycles):
        for low, high in ((100_000, 108_000), (108_000, 116_000)):
            rows.append(bar(index, low, high))
            index += 1
        for low, high in ((108_000, 116_000), (100_000, 108_000)):
            rows.append(bar(index, low, high))
            index += 1
    return rows


def test_more_than_one_trade_in_a_window() -> None:
    """🔴 창 하나에서 매매가 여러 번 나와야 한다.

    예전에는 as-of 계획 하나를 첫 청산까지만 굴려 **설계상 최대 1건**이었다.
    """
    rows = oscillation(12)
    atr = [Decimal(4_000)] * len(rows)
    trades = replay_all(rows, atr, tick=Decimal(0))
    assert len(trades) >= 2, f"매매 {len(trades)}건 — 연속 재생이 안 돌고 있다"


def test_trades_are_numbered_from_one() -> None:
    rows = oscillation(12)
    atr = [Decimal(4_000)] * len(rows)
    trades = replay_all(rows, atr, tick=Decimal(0))
    assert [item.number for item in trades] == list(range(1, len(trades) + 1))


def test_trades_do_not_overlap() -> None:
    """청산한 봉 **다음**부터 다음 매매를 찾는다 — 겹치면 같은 봉을 두 번 센다."""
    rows = oscillation(12)
    atr = [Decimal(4_000)] * len(rows)
    trades = replay_all(rows, atr, tick=Decimal(0))
    for earlier, later in pairwise(trades):
        assert earlier.marks[-1].index < later.marks[0].index


def test_flat_market_trades_nothing() -> None:
    """레벨이 안 서면 매매도 없다 — "0건"도 유효한 답이다."""
    rows = [bar(i, 100_000, 100_100) for i in range(80)]
    trades = replay_all(rows, [Decimal(500)] * len(rows), tick=Decimal(0))
    assert trades == []


def test_middle_trades_are_closed() -> None:
    """중간 매매는 청산까지 끝나 있어야 다음이 시작된다.

    ⚠️ **마지막 것만** 보유 중일 수 있다 — 창이 거기서 끝났기 때문이다. 끝난 척하지
    않는다 (절대 규칙 #8).
    """
    rows = oscillation(12)
    atr = [Decimal(4_000)] * len(rows)
    trades = replay_all(rows, atr, tick=Decimal(0))
    for item in trades[:-1]:
        assert item.closed
    assert all(item.marks[-1].event in (Event.STOP, Event.TP2) for item in trades if item.closed)


def test_trade_carries_marks() -> None:
    """세로선을 그리려면 사건마다 봉 번호와 가격이 있어야 한다."""
    rows = oscillation(12)
    trades = replay_all(rows, [Decimal(4_000)] * len(rows), tick=Decimal(0))
    assert trades, "표본이 없으면 이 테스트가 아무것도 안 지킨다"
    first: Trade = trades[0]
    assert first.marks[0].event is Event.FIRST
    assert first.marks[0].index >= 1
