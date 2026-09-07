"""체결을 봉 단위 델타로 접는다 — **순수 함수**.

## 왜 순수 함수인가

WS 수신은 부수효과이고 집계는 계산이다. 섞으면 테스트하려고 소켓을 띄워야 하고,
그러면 아무도 안 짠다. 여기는 **틱 목록 → 봉 델타 목록**만 한다.

## 봉 경계는 `timeframes.floor_to_interval` 을 쓴다

⛔ 여기서 따로 계산하지 않는다. 수집기와 무결성 검사기가 각자 경계를 구하면
"검사기는 288봉을 기대하는데 수집은 289봉을 넣는" 어긋남이 생긴다.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from updown.common.domain.instrument import Timeframe
from updown.common.domain.trade_tick import BarDelta, TradeSide, TradeTick
from updown.marketdata.ingest.timeframes import floor_to_interval


def fold(ticks: Iterable[TradeTick], timeframe: Timeframe) -> list[BarDelta]:
    """체결을 봉 단위로 접는다.

    Args:
        ticks: 체결. 순서는 상관없다 (시각으로 묶는다).
        timeframe: 접을 시간축.

    Returns:
        봉 시작 시각 오름차순 델타 목록.

    Note:
        여러 종목이 섞여 있어도 된다 — `(종목, 봉)` 으로 묶는다.

        ⚠️ **빈 봉을 만들지 않는다.** 체결이 없던 봉은 결과에 없다. 0 으로 채우면
        "거래가 없었다"와 "우리가 못 받았다"가 같아 보이고, 그 둘은 다른 사실이다
        (절대 규칙 #8).
    """
    buckets: dict[tuple[str, object], list[TradeTick]] = {}
    for tick in ticks:
        bar = floor_to_interval(tick.ts, timeframe)
        buckets.setdefault((tick.instrument.symbol, bar), []).append(tick)

    out: list[BarDelta] = []
    for (_, bar), group in buckets.items():
        buy = sum((t.volume for t in group if t.side is TradeSide.BUY), Decimal(0))
        sell = sum((t.volume for t in group if t.side is TradeSide.SELL), Decimal(0))
        out.append(
            BarDelta(
                instrument=group[0].instrument,
                timeframe=timeframe,
                ts=bar,  # pyright: ignore[reportArgumentType]
                buy_volume=buy,
                sell_volume=sell,
                trades=len(group),
            )
        )
    return sorted(out, key=lambda d: (d.instrument.symbol, d.ts))


def approximate_buy_ratio(
    open_: Decimal, high: Decimal, low: Decimal, close: Decimal
) -> Decimal | None:
    """캔들만으로 추정한 매수 비중 — **표준 근사식**.

    Args:
        open_: 시가. 지금 식은 안 쓰지만, 학습으로 대체할 때 후보 입력이라 받아 둔다.
        high: 고가.
        low: 저가.
        close: 종가.

    Returns:
        `(close - low) / (high - low)`. 고가와 저가가 같으면 None.

    Note:
        🔴 **이것은 기준선이지 정답이 아니다.** 종가가 고가에 붙으면 매수 우위로 보는
        발상인데, 그 가정이 우리 종목·시간축에서 맞는지는 **재 봐야 안다.**

        ⭐ `BarDelta.buy_ratio`(진짜 값)를 정답지로 두고 이 식이 얼마나 맞히는지
        측정한다. 안 맞으면 **실제 체결로 회귀해 식을 다시 뽑는다** — 그것이
        `trade_tick.py` 가 존재하는 이유다.

        ⛔ 고가=저가일 때 0.5 로 채우지 않는다. 방향을 모르는 봉이고, 0.5 로 채우면
        학습 데이터에 가짜 중립이 섞인다.
    """
    del open_
    span = high - low
    if span == 0:
        return None
    return (close - low) / span
