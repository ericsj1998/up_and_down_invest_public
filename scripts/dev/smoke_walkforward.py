"""모의 라이브 한 판을 **끝까지** 걸어간다 (T13 스모크).

## 화면 없이 먼저 도는지 본다

T13 의 목적은 사람이 보면서 걸어가는 것이지만, 그 전에 **엔진이 끝까지 도는지**를
알아야 한다. 화면을 붙인 뒤에 엔진이 멈추면 어느 쪽이 문제인지 못 가른다.

```
uv run python scripts/dev/smoke_walkforward.py 2024-03-02 7
```

⚠️ 여기서 나온 숫자를 성과로 인용하지 않는다. *"한 바퀴가 도는가"* 만 본다.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.playbook.select import default_playbook, load_playbooks
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.session import run_to_end

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
FRAMES = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1)
"""T13 ⑨ — 다루는 봉은 5m·15m·1h·4h·1d 다."""

WARMUP_BARS = 600
"""봉인 시작 **이전**에 확보할 봉 수.

🔴 추세·거래량 기준선은 창을 넘어선다. 워밍업 없이 시작하면 판정이 `None` 이라
플레이북이 영원히 0건이고, 그것을 "자리가 없었다"로 읽게 된다.
"""


async def load(moment: datetime, days: int) -> dict[Timeframe, list[Candle]]:
    """봉인 구간 + 워밍업을 시간축별로 받아 온다."""
    end = moment + timedelta(days=days)
    out: dict[Timeframe, list[Candle]] = {}
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(Market.UPBIT)
        for frame in FRAMES:
            rows = await adapter.get_candles(
                BTC, frame, moment - interval(frame) * WARMUP_BARS, end
            )
            out[frame] = list(rows)
    return out


async def main() -> None:
    """한 판 걸어간다."""
    day = sys.argv[1] if len(sys.argv) > 1 else "2024-03-02"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    moment = datetime.fromisoformat(f"{day}T00:00:00+00:00").astimezone(UTC)

    playbook = next(item for item in load_playbooks() if item.playbook_id == default_playbook())
    source = await load(moment, days)
    for frame, rows in source.items():
        print(
            f"   {frame.value:>4} {len(rows):>5}봉  {rows[0].ts:%Y-%m-%d} ~ {rows[-1].ts:%Y-%m-%d}"
        )

    seal = Seal(start=moment, end=moment + timedelta(days=days))
    session = Session(
        instrument=BTC,
        playbooks=(playbook,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000_000)),
    )
    print(f"\n=== {playbook.attribution} · {day} 부터 {days}일 봉인 ===")

    shots = run_to_end(session)
    book = session.ledger
    print(f"진행 {session.feed.progress() * 100:.0f}% · 신호가 난 봉 {len(shots)}개")
    print(f"매매 {len(book.records)}건 (청산 {len(book.closed)})")
    for item in book.records:
        planned = item.planned_rr
        got = item.achievement
        print(
            f"   {item.trade_id} {item.actor.value} {item.outcome.value:>4} "
            f"진입 {item.entry:>13,.0f} "
            f"계획RR {planned if planned is None else f'{planned:.2f}':>6} "
            f"달성률 {got if got is None else f'{got:.2f}':>6} "
            f"이득 {item.gain_pct if item.gain_pct is None else f'{item.gain_pct:.2f}%'}"
        )
    rate = book.win_rate
    mean = book.mean_achievement
    print(
        f"\n승률 {rate if rate is None else f'{rate:.1f}%'} · "
        f"평균 달성률 {mean if mean is None else f'{mean:.2f}'} · "
        f"금액 {book.cash:,.0f} ({book.return_pct:+.2f}%)"
    )
    top = sorted(session.detections.items(), key=lambda kv: -kv[1])[:5]
    print(f"근거 탐지: {' · '.join(f'{k} {v}' for k, v in top) or '없음'}")


if __name__ == "__main__":
    asyncio.run(main())
