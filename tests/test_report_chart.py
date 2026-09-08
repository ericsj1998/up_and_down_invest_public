"""리포트 차트 렌더 — PNG 를 실제로 뽑고 안 죽는지 (T55 · 사용자 2026-08-24).

막아야 하는 실패:
1. 🔴 봉이 없어도 첨부가 **PNG 여야** 한다 — 빈 바이트로 조용히 죽으면 메일이 깨진다.
2. 🔴 이득/손해 부호가 박스 색을 정한다 (`won`) — 아직 안 닫힌 매매는 색이 없다(None).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.orchestration.report.chart import (
    ChartCandle,
    ChartMeta,
    ChartTrade,
    render_run_chart,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
START = datetime(2026, 8, 23, tzinfo=UTC)


def _meta() -> ChartMeta:
    return ChartMeta(
        symbol="BTC_USDT",
        playbook="박스 0.70",
        period_label="24h",
        equity_before=Decimal("1000"),
        gain_pct=Decimal("0.7"),
        stops=1,
        takes=2,
    )


def _candles(n: int = 20) -> list[ChartCandle]:
    out: list[ChartCandle] = []
    for i in range(n):
        base = Decimal(100 + i)
        out.append(
            ChartCandle(
                ts=START + timedelta(minutes=15 * i),
                open=base,
                high=base + 2,
                low=base - 2,
                close=base + 1,
            )
        )
    return out


def test_renders_png_with_trades() -> None:
    trade = ChartTrade(
        direction="롱",
        entry=Decimal("105"),
        stop=Decimal("101"),
        target=Decimal("112"),
        first=Decimal("108"),
        opened_at=START + timedelta(minutes=15 * 3),
        closed_at=START + timedelta(minutes=15 * 12),
        exit=Decimal("111"),
        gain_pct=Decimal("1.5"),
        outcome="목표 익절",
    )
    png = render_run_chart(candles=_candles(), trades=[trade], meta=_meta())
    assert png.startswith(_PNG_MAGIC) and len(png) > 1000


def test_empty_candles_still_png() -> None:
    # 🔴 봉이 없어도 "봉 없음" 한 장 — 빈 첨부로 메일을 깨지 않는다.
    png = render_run_chart(candles=[], trades=[], meta=_meta())
    assert png.startswith(_PNG_MAGIC)


def test_won_sign() -> None:
    def trade(gain: Decimal | None) -> ChartTrade:
        return ChartTrade(
            direction="롱",
            entry=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            first=None,
            opened_at=START,
            closed_at=None if gain is None else START + timedelta(minutes=30),
            exit=None if gain is None else Decimal("103"),
            gain_pct=gain,
            outcome="보유중" if gain is None else "청산",
        )

    assert trade(Decimal("1")).won is True
    assert trade(Decimal("-1")).won is False
    assert trade(None).won is None
