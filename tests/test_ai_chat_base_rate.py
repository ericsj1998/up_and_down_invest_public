"""T270 #4 — 과거 빈도 도구: 구조 버킷 · 표본 수 · 문장에 "확률" 이 없다."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.ai_chat.base_rate import MIN_SAMPLE, base_rate, structure_at

NVDA = Instrument(Market.NASDAQ, "NVDA", "NVDA", AssetType.STOCK, Currency.USD)


def _wave(n: int, *, period: int = 40, amp: float = 10.0) -> list[Candle]:
    """사인파 봉 — 같은 구조가 주기마다 되돌아와 표본이 쌓인다 (결정론 · 규칙 #5)."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(n):
        price = Decimal(str(round(100 + amp * math.sin(2 * math.pi * i / period) + i * 0.01, 4)))
        out.append(
            Candle(
                instrument=NVDA,
                timeframe=Timeframe.D1,
                ts=base + timedelta(days=i),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal(1000),
            )
        )
    return out


def test_structure_buckets() -> None:
    got = structure_at(25.0, Decimal(90), Decimal(95), Decimal(100))
    assert got == ("RSI 30 미만", "SMA200 5% 넘게 아래", "20일선이 200일선 아래")
    assert structure_at(None, Decimal(1), Decimal(1), Decimal(1)) is None
    hot = structure_at(75.0, Decimal(110), Decimal(105), Decimal(100))
    assert hot is not None and hot[0] == "RSI 70 이상" and hot[1] == "SMA200 5% 넘게 위"


def test_base_rate_counts_same_structure_and_never_says_probability() -> None:
    got = base_rate(_wave(1200), horizon=20)
    assert got["n"] >= MIN_SAMPLE and got["grey"] is False
    assert got["up_pct"] is not None and abs(got["up_pct"] + got["down_pct"] - 100) < 0.2
    assert "확률" not in got["sentence"] and "n=" in got["sentence"]
    assert got["median_ret_pct"] is not None and got["worst_ret_pct"] <= got["best_ret_pct"]


def test_base_rate_is_grey_or_silent_when_short() -> None:
    assert "note" in base_rate(_wave(100)) and "n" not in base_rate(_wave(100))
    got = base_rate(_wave(260), horizon=20)
    assert (got["grey"] is True and "표본 부족" in got["sentence"]) or got["n"] == 0
