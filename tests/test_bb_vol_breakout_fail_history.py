"""T335 A1 — 돌파 롱 탐지기의 가짜 돌파 이력 문(`fail_history_max`).

문 정의(연구 `fakebreak.features.a1_fail_history` 와 같다): 직전 `fail_history_bars` 봉 안에서
"그때의 `bb_period` 봉 고가를 종가로 넘었다가 `fail_history_back` 봉 안에 다시 아래로 마감" 한
봉 수가 `fail_history_max` 보다 크면 안 든다. -1 = 문 없음(지금 실계좌).
연구 쪽 답(606 ~ 611)은 핵심 6 에서 값이 없고 알트에서만 단조 음수라, 이 문은 펀드 재현(Gate 40)으로
알트 우주에서 재기 위한 스위치다 — 켜는 것은 사용자 결정.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.detectors.private_strategy import private_strategy, fail_history
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

INSTRUMENT = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ROUND_TRIP = Decimal("0.002")


def bar(
    i: int, close: Decimal, *, volume: Decimal = Decimal(100), high: Decimal | None = None
) -> Candle:
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.H1,
        ts=T0 + timedelta(hours=i),
        open=close - Decimal("0.5"),
        high=high if high is not None else close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=volume,
    )


def window_with_fake(n: int = 200, fake_at: int | None = 170) -> list[Candle]:
    """완만한 상승 뒤 마지막 봉이 밴드 밖으로 크게 뛴 창.

    `fake_at` 에 가짜 돌파 하나(종가로 20봉 고가를 넘고 다음 봉 되마감).
    되돌아보기 48봉 안이어야 센다.
    """
    rows = [bar(i, Decimal(100) + Decimal(i) * Decimal("0.05")) for i in range(n - 1)]
    if fake_at is not None:
        base = rows[fake_at].close
        rows[fake_at] = bar(
            fake_at, base + Decimal(4)
        )  # 그때 20봉 고가(≈ base + 1)를 종가로 넘는다
        rows[fake_at + 1] = bar(fake_at + 1, base)  # 다음 봉 되마감
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


class TestFailHistoryCount:
    def test_counts_one_fake_in_lookback(self) -> None:
        w = window_with_fake()
        assert fail_history(w, bars=48, back=5, look=20) == 1

    def test_zero_when_no_fake(self) -> None:
        w = window_with_fake(fake_at=None)
        assert fail_history(w, bars=48, back=5, look=20) == 0

    def test_fake_outside_lookback_is_not_counted(self) -> None:
        w = window_with_fake(fake_at=60)
        assert fail_history(w, bars=48, back=5, look=20) == 0


class TestGate:
    def test_off_by_default_keeps_the_setup(self) -> None:
        assert setup_of(window_with_fake()) is not None

    def test_max_zero_blocks_when_history_exists(self) -> None:
        assert setup_of(window_with_fake(), fail_history_max=0) is None

    def test_max_zero_allows_when_no_history(self) -> None:
        assert setup_of(window_with_fake(fake_at=None), fail_history_max=0) is not None

    def test_max_one_allows_one_fake(self) -> None:
        assert setup_of(window_with_fake(), fail_history_max=1) is not None
