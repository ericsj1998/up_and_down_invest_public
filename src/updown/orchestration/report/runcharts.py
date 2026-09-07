"""판별 차트 재료 — 원장 기록 → ChartTrade · DB 캔들 → ChartCandle (T55).

`chart.py` 는 그리기만 안다. 여기가 **원장·DB 를 차트 자료형으로 옮긴다** — 경계를 지켜
차트가 DB 를 모르게 한다 (규칙 #1 레이어). 값은 전부 사실이고, 없으면 지어내지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa

from updown.common.db.models.market import Candle as CandleRow
from updown.common.db.models.master import Instrument as InstrumentRow
from updown.orchestration.report.chart import ChartCandle, ChartMeta, ChartTrade

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from updown.common.domain.instrument import Market, Timeframe
    from updown.orchestration.report.performance import Window
    from updown.orchestration.walkforward.ledger import TradeRecord

# 결말 분류 — 익절 계열 · 손절 계열. 나머지(전환·레벨 이탈 등)는 어느 쪽도 안 센다.
_TAKE = ("익절",)  # "목표 익절"·"전환 익절"·"반익반본" 은 "익절" 을 품는다
_STOP = ("손절", "강제청산")


def to_chart_trade(record: TradeRecord) -> ChartTrade | None:
    """원장 기록 하나를 차트 매매로 옮긴다.

    Args:
        record: 라이브 판의 매매 기록.

    Returns:
        차트 매매. **아직 안 채워진**(체결 시각 없음) 계획은 그릴 자리가 없어 None.

    Note:
        방향·결말은 이미 한국어 StrEnum 이라 그대로 문자열로 쓴다. 청산가·손익은
        기록의 파생 속성(`exit_average`·`gain_pct`)에서 오고, 없으면 None 그대로 둔다.
    """
    if record.opened_at is None:
        return None
    return ChartTrade(
        direction=str(record.direction),
        entry=record.entry,
        stop=record.planned_stop,
        target=record.planned_target,
        first=record.planned_first if record.planned_first != record.planned_target else None,
        opened_at=record.opened_at,
        closed_at=record.closed_at,
        exit=record.exit_average,
        gain_pct=record.gain_pct,
        outcome=str(record.outcome),
    )


def meta_for(
    *,
    symbol: str,
    playbook: str,
    window: Window,
    timeframe: str,
    records: Sequence[TradeRecord],
    equity_before: Decimal | None,
) -> ChartMeta:
    """제목줄 수치를 원장 기록에서 뽑는다 (구간 손익률·익절/손절 횟수).

    Args:
        symbol: 종목.
        playbook: 매매법 이름(들).
        window: 집계 구간.
        timeframe: 봉 축 라벨 (예: "15m").
        records: 이 판의 매매들. 손익률은 **청산된 것만** 더한다.
        equity_before: 구간 시작 시점 금액. 못 읽으면 None.

    Returns:
        차트 제목줄 재료.
    """
    closed = [r for r in records if r.closed_at is not None]
    gains = [r.gain_pct for r in closed if r.gain_pct is not None]
    takes = sum(1 for r in closed if any(k in str(r.outcome) for k in _TAKE))
    stops = sum(1 for r in closed if str(r.outcome) in _STOP)
    period = f"{window.since:%Y-%m-%d %H:%M} ~ {window.until:%m-%d %H:%M} UTC · {timeframe}"
    return ChartMeta(
        symbol=symbol,
        playbook=playbook,
        period_label=period,
        equity_before=equity_before,
        gain_pct=sum(gains, Decimal(0)) if gains else None,
        stops=stops,
        takes=takes,
    )


async def load_candles(
    factory: async_sessionmaker[AsyncSession],
    *,
    market: Market,
    symbol: str,
    timeframe: Timeframe,
    window: Window,
) -> list[ChartCandle]:
    """구간의 봉을 DB 에서 읽어 차트 캔들로 옮긴다.

    Args:
        factory: DB 세션 팩토리.
        market: 거래소 (`Market.GATE`·`Market.UPBIT`). 문자열이면 `Market(값)` 로 만든다.
        symbol: 종목 (예: "BTC_USDT").
        timeframe: 봉 축.
        window: 구간 (끝 미포함).

    Returns:
        시각 오름차순 봉들. 없으면 빈 리스트 — 차트는 "봉 없음" 을 그린다.
    """
    async with factory() as session:
        rows = (
            await session.execute(
                sa.select(
                    CandleRow.ts, CandleRow.open, CandleRow.high, CandleRow.low, CandleRow.close
                )
                .join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
                .where(InstrumentRow.market == market)
                .where(InstrumentRow.symbol == symbol)
                .where(CandleRow.timeframe == timeframe)
                .where(CandleRow.ts >= window.since)
                .where(CandleRow.ts < window.until)
                .order_by(CandleRow.ts)
            )
        ).all()
    return [ChartCandle(ts=ts, open=o, high=h, low=lo, close=c) for ts, o, h, lo, c in rows]
