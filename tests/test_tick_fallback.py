"""판정 창이 비어도 **체결 흡수는 멎지 않는다** (T72 §10~§13).

## 무엇이 났었나

라이브에서 지정가가 채워졌는데 원장이 그것을 **세 판정 연속으로** 못 적었다
(2026-08-28 · 고아 8건 · 손절도 안 걸림). `Session._collect` 가 이렇게 시작한다:

```
if waiting is None or self.filler is None:
    return
bar = self._tick()
if bar is None:
    return  # ← 여기서 매번 나갔다
self.waiting += 1
```

`_waiting` 을 비우는 유일한 코드가 저 카운터 **뒤**에 있으므로, 카운터가 0 인 채
고아가 났다는 것은 **`_tick()` 이 None 이었다**는 뜻이다 (소거법).

방아쇠 축을 선언 안 한 룰(추세·캐리)은 `judged(5m)` 을 보는데, 커서가 4h 에 묶여
있는 동안 그 창이 통째로 커서 뒤로 흘러가면 빈다. 그러면 **거래소는 채웠고 원장은
모르는** 상태가 되고, 손절도 안 걸린 3배 포지션이 남는다.

## 이 파일이 지키는 것

1. 판정 창이 비어도 관측 창으로 대체해 봉을 준다 (라이브)
2. **봉인 급전은 한 비트도 안 변한다** — `observed()` 가 `judged()` 라 비면 그대로 None
"""

from __future__ import annotations

from collections.abc import Sequence
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
from updown.orchestration.walkforward.live_feed import LiveFeed

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
ENTRY = Timeframe.H4


def _c(frame: Timeframe, ts: datetime) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ts,
        open=Decimal(525),
        high=Decimal(538),
        low=Decimal(522),
        close=Decimal(535),
        volume=Decimal(10),
    )


def _book() -> Playbook:
    return Playbook(
        playbook_id="trend",
        version="0",
        market_groups=(MarketGroup.COIN,),
        timeframe=ENTRY,
        regimes=(),
        primary_family=Family.TREND,
        setups=(),
    )


def _live_session() -> Session:
    """커서가 4h 에 묶여 있고 **5m 창이 통째로 커서 뒤에 있는** 라이브 급전."""
    seed: dict[Timeframe, Sequence[Candle]] = {
        ENTRY: [_c(ENTRY, START + timedelta(hours=4 * i)) for i in range(3)],
        # 🔴 5m 시드를 **커서 이후로만** 준다 — 라이브에서 창이 흘러간 뒤의 모양이다.
        Timeframe.M5: [
            _c(Timeframe.M5, START + timedelta(hours=12, minutes=5 * (i + 1))) for i in range(6)
        ],
    }
    feed = LiveFeed(seed, ENTRY)
    return Session(
        instrument=BTC,
        playbooks=(_book(),),
        feed=feed,
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )


def test_live_tick_falls_back_when_judged_window_is_empty() -> None:
    """🔴 판정 창이 비어도 봉을 준다 — 이게 없으면 체결 흡수가 통째로 멎는다."""
    session = _live_session()
    assert session.price_frame is None, "전제: 추세 룰은 방아쇠 축을 선언 안 한다"
    assert session.feed.judged(Timeframe.M5) == [], "전제: 판정 창이 비어 있어야 한다"
    assert session.feed.observed(Timeframe.M5), "전제: 관측 창에는 봉이 있다"

    bar = session._tick()  # pyright: ignore[reportPrivateUsage]
    assert bar is not None, (
        "판정 창이 비었다고 None 을 주면 `_collect` 가 우편함을 안 연다 — "
        "거래소는 채웠고 원장은 모르는 고아가 된다 (T72 §12 · 3판정 연속 8건)"
    )


def test_live_tick_prefers_the_judged_window_when_it_has_bars() -> None:
    """⚠️ 대체는 **비었을 때만**이다 — 있으면 판정 창을 그대로 쓴다 (동결 유지)."""
    seed: dict[Timeframe, Sequence[Candle]] = {
        ENTRY: [_c(ENTRY, START + timedelta(hours=4 * i)) for i in range(3)],
        Timeframe.M5: [_c(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(80)],
    }
    session = Session(
        instrument=BTC,
        playbooks=(_book(),),
        feed=LiveFeed(seed, ENTRY),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )
    judged = session.feed.judged(Timeframe.M5)
    assert judged, "전제: 판정 창에 봉이 있다"
    bar = session._tick()  # pyright: ignore[reportPrivateUsage]
    assert bar is not None
    assert bar.ts == judged[-1].ts, "판정 창이 있으면 그것을 쓴다 — 관측 창으로 새지 않는다"


def test_sealed_feed_is_untouched() -> None:
    """⭐ 백테스트는 한 비트도 안 변한다 — `SealedFeed.observed()` 가 `judged()` 다.

    판정 창이 비면 관측 창도 비므로 대체가 아무 일도 안 한다. 이 성질이 깨지면
    백테스트가 **미래를 본다** — 그래서 테스트로 못 박는다.
    """
    source = {
        ENTRY: [_c(ENTRY, START + timedelta(hours=4 * i)) for i in range(12)],
        Timeframe.M5: [_c(Timeframe.M5, START + timedelta(minutes=5 * i)) for i in range(600)],
    }
    seal = Seal(start=START, end=START + timedelta(hours=4))
    feed = SealedFeed(source, seal)
    for frame in (ENTRY, Timeframe.M5):
        seen = [item.ts for item in feed.observed(frame)]
        judged = [item.ts for item in feed.judged(frame)]
        assert seen == judged, "봉인 급전에서 관측과 판정이 갈리면 백테스트가 미래를 본다"
