"""T359 상위 봉 이평 띠 크기(`SmaTilt`) — 연구 `fakebreak.features` 와 같은 식인지 · 선언 파서."""

from __future__ import annotations

from decimal import Decimal

import pytest

from updown.analysis.playbook.select import PlaybookConfigError, sma_tilts
from updown.analysis.playbook.types import SmaTilt
from updown.common.domain.instrument import Timeframe


def _closes(values: list[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


def test_distance_matches_research_definition() -> None:
    """거리 = 마지막 종가 ÷ SMA(마지막 period 개) - 1 (%) — `sma_dist_raw` 와 같다."""
    rule = SmaTilt(timeframe=Timeframe.D1, period=3, mult=Decimal("0.75"), low=Decimal(0))
    closes = _closes([1, 2, 3, 4, 5, 6])
    # SMA(4, 5, 6) = 5 → 6 / 5 - 1 = 20%
    assert rule.value(closes) == Decimal(20)


def test_slope_matches_research_definition() -> None:
    """기울기 = SMA(지금) ÷ SMA(back 봉 전) - 1 (%) — `sma_slope_band` 와 같다."""
    rule = SmaTilt(timeframe=Timeframe.D1, period=2, back=2, mult=Decimal(0), low=Decimal(-100))
    closes = _closes([10, 10, 20, 20])
    # 지금 SMA(20, 20) = 20 · 2봉 전 SMA(10, 10) = 10 → +100%
    assert rule.value(closes) == Decimal(100)


def test_value_unknown_when_short() -> None:
    rule = SmaTilt(timeframe=Timeframe.H4, period=20, mult=Decimal("0.5"), low=Decimal("-3.66"))
    assert rule.value(_closes([1.0] * 19)) is None


def test_band_is_half_open() -> None:
    rule = SmaTilt(
        timeframe=Timeframe.D1,
        period=20,
        mult=Decimal("0.75"),
        low=Decimal("3.994"),
        high=Decimal("9.249"),
    )
    assert rule.holds(Decimal("3.994"))
    assert not rule.holds(Decimal("9.249"))
    assert not rule.holds(Decimal("3.99"))


def test_open_ended_band() -> None:
    rule = SmaTilt(timeframe=Timeframe.H4, period=20, mult=Decimal(0), low=Decimal("0.66"))
    assert rule.holds(Decimal(50))
    assert not rule.holds(Decimal("0.65"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"period": 1, "mult": Decimal(1), "low": Decimal(0)},
        {"period": 20, "mult": Decimal(-1), "low": Decimal(0)},
        {"period": 20, "mult": Decimal(1)},
        {"period": 20, "mult": Decimal(1), "low": Decimal(2), "high": Decimal(1)},
    ],
)
def test_invalid_declarations_raise(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        SmaTilt(timeframe=Timeframe.D1, **kwargs)


def test_parser_reads_list() -> None:
    got = sma_tilts(
        [
            {"timeframe": "1d", "period": 20, "low": "3.994", "high": "9.249", "mult": "0.75"},
            {
                "timeframe": "1d",
                "period": 50,
                "back": 5,
                "low": "-1.946",
                "high": "-0.629",
                "mult": 0,
            },
        ],
        "playbooks.x",
    )
    assert len(got) == 2
    assert got[0].timeframe is Timeframe.D1
    assert got[1].back == 5
    assert got[1].mult == 0


def test_parser_rejects_non_list() -> None:
    with pytest.raises(PlaybookConfigError):
        sma_tilts({"timeframe": "1d"}, "playbooks.x")
