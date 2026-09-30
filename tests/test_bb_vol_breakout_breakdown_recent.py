"""T335 A2 — 돌파 롱 탐지기의 하방 이탈 뒤 돌파(V 자) 크기 문(`breakdown_recent_bars`).

문 정의(연구 `fakebreak.features.a2_recent` 와 같다): 직전 `breakdown_recent_bars` 봉 안에서
"그때의 `bb_period` 봉 최저가를 저가로 뚫은" 봉이 하나라도 있으면 `size_mult` 에
`breakdown_recent_mult` 를 곱한다(0 = 건너뜀). 0 봉 = 문 없음(지금 실계좌).
연구 답(613 ~ 628): within 3 ~ 6 x0.25 가 CB0 위 ✅★ · 부트스트랩 3 · 4 · 5 통과 — 세션 · 펀드
재현으로 실계좌 우주에서 재기 위한 스위치다. 켜는 것은 사용자 결정.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.detectors.private_strategy import private_strategy, breakdown_recent
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

INSTRUMENT = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ROUND_TRIP = Decimal("0.002")


def bar(
    i: int, close: Decimal, *, volume: Decimal = Decimal(100), low: Decimal | None = None
) -> Candle:
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.H1,
        ts=T0 + timedelta(hours=i),
        open=close - Decimal("0.5"),
        high=close + Decimal("1"),
        low=low if low is not None else close - Decimal("1"),
        close=close,
        volume=volume,
    )


def window_with_breakdown(n: int = 200, breakdown_at: int | None = 195) -> list[Candle]:
    """완만한 상승 뒤 마지막 봉이 밴드 밖으로 크게 뛴 창.

    `breakdown_at` 봉의 저가가 그때의 20봉 최저가 아래로 찔린다(V 자). 마지막 봉은 n-1 이라
    within 5 안이면 195 ~ 198 이 걸린다.
    """
    rows = [bar(i, Decimal(100) + Decimal(i) * Decimal("0.05")) for i in range(n - 1)]
    if breakdown_at is not None:
        base = rows[breakdown_at].close
        rows[breakdown_at] = bar(
            breakdown_at, base, low=base - Decimal(5)
        )  # 20봉 최저가(≈ base - 2) 아래
    last_close = rows[-1].close + Decimal(6)
    rows.append(bar(n - 1, last_close, volume=Decimal(300)))
    return rows


def setup_of(window: list[Candle], **extra: object):
    return private_strategy(
        window,
        Timeframe.H1,
        ROUND_TRIP,
        bb_period=20,
        bb_k=Decimal(2),
        vol_period=20,
        vol_multiple=Decimal("2.0"),
        sl_atr=Decimal("0.2"),
        dir_period=20,
        dir_bars=5,
        **extra,  # type: ignore[arg-type]
    )


class TestBreakdownCount:
    def test_counts_one_breakdown_in_window(self) -> None:
        w = window_with_breakdown()
        assert breakdown_recent(w, within=5, look=20) == 1

    def test_zero_when_no_breakdown(self) -> None:
        w = window_with_breakdown(breakdown_at=None)
        assert breakdown_recent(w, within=5, look=20) == 0

    def test_breakdown_outside_window_is_not_counted(self) -> None:
        w = window_with_breakdown(breakdown_at=150)
        assert breakdown_recent(w, within=5, look=20) == 0
        assert breakdown_recent(w, within=60, look=20) == 1


class TestGate:
    def test_off_by_default_keeps_full_size(self) -> None:
        made = setup_of(window_with_breakdown())
        assert made is not None and made.size_mult == Decimal(1)

    def test_scales_size_when_breakdown_is_recent(self) -> None:
        made = setup_of(window_with_breakdown(), breakdown_recent_bars=5)
        assert made is not None and made.size_mult == Decimal("0.25")

    def test_full_size_when_breakdown_is_old(self) -> None:
        made = setup_of(window_with_breakdown(breakdown_at=150), breakdown_recent_bars=5)
        assert made is not None and made.size_mult == Decimal(1)

    def test_custom_multiplier(self) -> None:
        made = setup_of(
            window_with_breakdown(), breakdown_recent_bars=5, breakdown_recent_mult=Decimal("0.5")
        )
        assert made is not None and made.size_mult == Decimal("0.5")

    def test_zero_multiplier_skips(self) -> None:
        assert (
            setup_of(
                window_with_breakdown(), breakdown_recent_bars=5, breakdown_recent_mult=Decimal(0)
            )
            is None
        )
