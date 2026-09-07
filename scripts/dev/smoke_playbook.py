"""플레이북 한 바퀴 — **국면 → 매매법 → 자리 → 계획 → 재생** 이 통째로 도는가.

## 왜 스크립트인가

측정이 아니다. *"끊긴 데가 어디인가"* 를 한 줄씩 찍어 보는 도구다. 성적을 재는 것은
[T13 모의 라이브](../docs/planning/tasks/T13_paper_walkforward.md) 가 한다 —
여기서 나온 숫자를 성과로 인용하지 않는다.

```
uv run python scripts/dev/smoke_playbook.py 2025-06-08 2024-03-02
```
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from updown.analysis.context.guard import AsOfSequence
from updown.analysis.detectors.base import MarketContext
from updown.analysis.indicators import snapshot as indicator_snapshot
from updown.analysis.playbook.select import active_playbooks, load_playbooks
from updown.analysis.structures.level_book import build_levels, roles_at
from updown.analysis.structures.replay import replay_all
from updown.analysis.trend.service import evaluate as trend_evaluate
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.playbook_run import propose

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
FRAME = Timeframe.M15
"""🔴 **선언과 같은 시간축이어야 한다.** 1h 로 돌렸더니 전부 "후보 없음" 이었는데,
선언이 15m 이라 `runs_in` 이 막은 것이었다 — 층은 제대로 돌았지만 화면에 이유가 없으면
"매매법이 죽었다"로 읽힌다. 그래서 아래에서 선언을 먼저 찍는다."""

BARS = 200
WARMUP = 400
"""추세 워밍업. 창만으로는 판정이 `None` 이라 플레이북이 **영원히 0건**이 된다."""


async def one(moment: datetime) -> None:
    """한 시점을 굴린다."""
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(Market.UPBIT)
        got = await adapter.get_candles(
            BTC, FRAME, moment - interval(FRAME) * (BARS + WARMUP), moment
        )
    closed = list(AsOfSequence.until(got, moment, FRAME))
    window = closed[-BARS:]
    history = trend_evaluate(BTC, FRAME, closed)
    state = history.states[-1] if history.states else None

    series = indicator_snapshot.compute(window)
    book = build_levels(window, series.atr14)
    roles = roles_at(book, window[-1].close, at=len(window) - 1)
    has_box = roles.upper is not None and roles.lower is not None

    ctx = MarketContext(
        instrument=BTC,
        as_of=moment,
        candles={FRAME: window},
        indicators={FRAME: series.at(len(window) - 1)},
        structures=(),
        geometry={},
        trend={FRAME: state} if state is not None else {},
    )
    found = propose(ctx, timeframe=FRAME, has_box=has_box)
    # ⛔ "후보 없음" 한 줄로는 **매매법이 안 돈 것**과 **돌았는데 자리가 없는 것**이
    #    구별되지 않는다. 둘은 고칠 곳이 완전히 다르다 (절대 규칙 #8).
    ran = active_playbooks(
        load_playbooks(),
        market_group=MarketGroup.of(BTC.market),
        timeframe=FRAME,
        trend=state.state if state is not None else None,
        has_box=has_box,
    )

    label = state.state.value if state is not None else "모름"
    print(f"\n=== {moment:%Y-%m-%d} · 추세 {label} · 박스 {'있음' if has_box else '없음'} ===")
    if not found:
        why = (
            f"매매법 {len(ran)}개가 돌았는데 자리가 없다"
            if ran
            else "이 자리 조건에 맞는 매매법이 없다 (국면·시간축·시장 중 하나가 안 맞다)"
        )
        print(f"   후보 없음 — {why}")
    for item in found:
        setup = item.setup
        print(f"   [{item.playbook.attribution}] {setup.setup_type} RR {setup.rr_ratio:.2f}")
        print(f"      진입 {setup.avg_entry:,.0f} · 손절 {setup.stop_loss:,.0f}")
        if item.conflicts:
            print(f"      ⚠️ 충돌 {' · '.join(item.conflicts)}")
    trades = replay_all(window, series.atr14)
    done = [t for t in trades if t.closed]
    print(f"   재생: 매매 {len(trades)}건 (청산 {len(done)})")


async def main() -> None:
    """인자로 받은 시점들을 차례로."""
    print("선언된 매매법")
    for item in load_playbooks():
        regimes = "·".join(regime.value for regime in item.regimes)
        setups = "·".join(item.setups)
        print(f"   {item.attribution}  {item.timeframe.value}  [{regimes}]  → {setups}")
    print(f"지금 도는 시간축: {FRAME.value}")

    days = sys.argv[1:] or ["2025-06-08"]
    for day in days:
        await one(datetime.fromisoformat(f"{day}T00:00:00+00:00").astimezone(UTC))


if __name__ == "__main__":
    asyncio.run(main())
