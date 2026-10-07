"""T406 CL0 · DB 실계좌 배선 — `BarTilt` 가 연구 `t401_entry_census.feats` 와 같은 식인지."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from updown.analysis.playbook.select import PlaybookConfigError, bar_tilts, load_playbooks
from updown.analysis.playbook.types import BarTilt, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward.session import Session

H4 = timedelta(hours=4)
T0 = datetime(2026, 10, 1, tzinfo=UTC)
BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)


def _books() -> dict[str, Playbook]:
    return {b.playbook_id: b for b in load_playbooks()}


def _bar(k: int, o: float, h: float, lo: float, c: float, tf: Timeframe = Timeframe.H4) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=tf,
        ts=T0 + k * H4,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal(1),
    )


def _series(n: int) -> list[Candle]:
    return [_bar(k, 100 + k, 103 + k, 98 + k, 101 + k) for k in range(n)]


def _research_atr_pct(bars: list[Candle], period: int = 14) -> float:
    """연구 `Sym` 그대로 — tr[0] = 고 - 저 · 뒤는 표준 TR · `sma(tr, 14)` ÷ 종가 x 100."""
    c = [float(b.close) for b in bars]
    tr = [float(bars[0].high - bars[0].low)] + [
        max(
            float(bars[i].high - bars[i].low),
            abs(float(bars[i].high) - c[i - 1]),
            abs(float(bars[i].low) - c[i - 1]),
        )
        for i in range(1, len(bars))
    ]
    return sum(tr[-period:]) / period / c[-1] * 100


def test_atr_pct_matches_research() -> None:
    bars = _series(40)
    rule = BarTilt(
        timeframe=Timeframe.H4, feature="atr_pct", mult=Decimal(0), high=Decimal("3.1252")
    )
    got = rule.value(bars)
    assert got is not None
    assert float(got) == pytest.approx(_research_atr_pct(bars), rel=1e-12)


def test_body_ratio_matches_research() -> None:
    rule = BarTilt(
        timeframe=Timeframe.H4, feature="body_ratio", mult=Decimal("0.5"), high=Decimal("0.3092")
    )
    bars = [_bar(0, 100, 110, 90, 104)]
    assert rule.value(bars) == Decimal("0.2")  # |104 - 100| / (110 - 90)
    flat = [_bar(0, 100, 100, 100, 100)]
    assert rule.value(flat) == Decimal(0)


def test_atr_unknown_when_short() -> None:
    rule = BarTilt(timeframe=Timeframe.H4, feature="atr_pct", mult=Decimal(0), high=Decimal(3))
    assert rule.value(_series(14)) is None
    assert rule.value(_series(15)) is not None


def test_band_half_open() -> None:
    rule = BarTilt(
        timeframe=Timeframe.H4, feature="atr_pct", mult=Decimal(0), high=Decimal("3.1252")
    )
    assert rule.holds(Decimal("3.1251"))
    assert not rule.holds(Decimal("3.1252"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"feature": "rsi", "mult": Decimal(1), "high": Decimal(1)},
        {"feature": "atr_pct", "mult": Decimal(-1), "high": Decimal(1)},
        {"feature": "atr_pct", "mult": Decimal(1)},
        {"feature": "atr_pct", "mult": Decimal(1), "low": Decimal(2), "high": Decimal(1)},
        {"feature": "atr_pct", "mult": Decimal(1), "high": Decimal(1), "period": 0},
    ],
)
def test_invalid_declarations_raise(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        BarTilt(timeframe=Timeframe.H4, **kwargs)


def test_parser() -> None:
    got = bar_tilts(
        [{"timeframe": "4h", "feature": "atr_pct", "period": 14, "high": "3.1252", "mult": "0"}],
        "x",
    )
    assert got == (
        BarTilt(
            timeframe=Timeframe.H4,
            feature="atr_pct",
            period=14,
            high=Decimal("3.1252"),
            mult=Decimal(0),
        ),
    )
    with pytest.raises(PlaybookConfigError):
        bar_tilts({"timeframe": "4h"}, "x")
    with pytest.raises(PlaybookConfigError):
        bar_tilts([{"timeframe": "4h", "feature": "nope", "high": "1", "mult": "1"}], "x")


def test_live_declarations() -> None:
    """실계좌 다리 — CL0 은 급락 되돌림 · DB 는 일봉 채널에만 · 다른 다리는 동결."""
    books = _books()
    assert books["private_strategy"].bar_tilts == (
        BarTilt(
            timeframe=Timeframe.H4,
            feature="atr_pct",
            period=14,
            high=Decimal("3.1252"),
            mult=Decimal(0),
        ),
    )
    assert books["private_strategy"].bar_tilts == (
        BarTilt(
            timeframe=Timeframe.H4,
            feature="body_ratio",
            high=Decimal("0.3092"),
            mult=Decimal("0.5"),
        ),
    )
    for name in (
        "private_strategy",
        "private_strategy",
        "private_strategy",
        "private_strategy",
    ):
        assert books[name].bar_tilts == ()
    # 귀속 키 — 선언 버전은 그대로(돌던 펀드 다리 저장본이 이긴다)
    assert books["private_strategy"].version == "0.1.0"
    assert books["private_strategy"].version == "0.1.0"


def _fake(bars: list[Candle]) -> SimpleNamespace:
    counts: dict[str, int] = {}

    def judged(_tf: Timeframe) -> list[Candle]:
        return bars

    def count(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    feed = SimpleNamespace(timeframes=(Timeframe.H4, Timeframe.D1), judged=judged)
    return SimpleNamespace(feed=feed, _count=count, counts=counts)


def test_session_private_strategy() -> None:
    """급락 되돌림(4H) — 신호봉 자신의 ATR% < 3.1252 면 0(건너뜀) · 아니면 1."""
    book = _books()["private_strategy"]
    calm = _series(30)  # ATR% ≈ 4 / 130 x 100 ≈ 3.1 근처 → 계산값으로 판정 확인
    sig = calm[-1]
    fake = _fake(calm)
    got = Session._bar_tilt_mult(fake, book, sig)  # type: ignore[arg-type]
    atr = _research_atr_pct(calm)
    assert got == (Decimal(0) if atr < 3.1252 else Decimal(1))
    wild = [_bar(k, 100, 110, 90, 100) for k in range(30)]  # ATR% 20 → 1 배
    assert Session._bar_tilt_mult(_fake(wild), book, wild[-1]) == Decimal(1)  # type: ignore[arg-type]
    tight = [_bar(k, 100, 101, 99.5, 100) for k in range(30)]  # ATR% 1.5 → 건너뜀
    assert Session._bar_tilt_mult(_fake(tight), book, tight[-1]) == Decimal(0)  # type: ignore[arg-type]


def test_session_daily_channel_uses_4h_bar_closed_at_midnight() -> None:
    """일봉 채널(1D) — 일봉 끝(UTC 00:00)에 닫힌 4H 봉(20:00 ~ 24:00) 몸통 · 없으면 1 배."""
    book = _books()["private_strategy"]
    day = datetime(2026, 10, 6, tzinfo=UTC)
    sig = Candle(
        instrument=BTC,
        timeframe=Timeframe.D1,
        ts=day,
        open=Decimal(1),
        high=Decimal(2),
        low=Decimal(1),
        close=Decimal(2),
        volume=Decimal(1),
    )
    small = Candle(
        instrument=BTC,
        timeframe=Timeframe.H4,
        ts=day + timedelta(hours=20),
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=Decimal(101),
        volume=Decimal(1),
    )
    big = Candle(
        instrument=BTC,
        timeframe=Timeframe.H4,
        ts=day + timedelta(hours=16),
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=Decimal(109),
        volume=Decimal(1),
    )
    assert Session._bar_tilt_mult(_fake([big, small]), book, sig) == Decimal("0.5")  # type: ignore[arg-type]
    late = _fake([big])  # 20:00 봉이 아직 없음 → 앞 봉(16:00)으로 재지 않는다
    assert Session._bar_tilt_mult(late, book, sig) == Decimal(1)  # type: ignore[arg-type]
    assert late.counts == {"bar_tilt:0:stale": 1}
    later = Candle(
        instrument=BTC,
        timeframe=Timeframe.H4,
        ts=day + timedelta(hours=24),
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=Decimal(101),
        volume=Decimal(1),
    )
    # 신호봉 끝 뒤에 닫힌 봉은 보지 않는다(미래 참조 없음)
    assert Session._bar_tilt_mult(_fake([big, small, later]), book, sig) == Decimal("0.5")  # type: ignore[arg-type]


def test_no_declaration_is_frozen() -> None:
    book = _books()["private_strategy"]
    fake = _fake(_series(30))
    assert Session._bar_tilt_mult(fake, book, _series(30)[-1]) == Decimal(1)  # type: ignore[arg-type]
    assert fake.counts == {}
