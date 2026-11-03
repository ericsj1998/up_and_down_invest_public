"""분기 우주 펀드 필드 (T758 5-1) — 선언 `universe` 키 · 종목별 진입 범위 기본값."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any, cast

import yaml

from updown.analysis.playbook.select import DEFAULT_CONFIG_PATH, parse_block
from updown.apps.api.rebalancer import _legs_of_symbol  # pyright: ignore[reportPrivateUsage]
from updown.orchestration.rebalancer.legs import FundLeg

LEG = "private_strategy"


def _leg(name: str, *symbols: str) -> FundLeg:
    return FundLeg(
        playbook=name,
        attribution=f"{name}@0",
        symbols=symbols,
        leverage=Decimal(4),
        exposure=Decimal(4),
        timeframe="4h",
        slots=6,
    )


def test_universe_key_is_parsed_and_absent_means_fixed_basket() -> None:
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    body = copy.deepcopy(cast("dict[str, Any]", raw["playbooks"][LEG]))
    assert parse_block(LEG, body).universe is None
    body["universe"] = "c0_literal"
    assert parse_block(LEG, body).universe == "c0_literal"


def test_legs_of_symbol_is_sorted_leg_ids_carrying_it() -> None:
    legs = (_leg("tri", "A_USDT", "B_USDT"), _leg("macd", "B_USDT"), _leg("dch", "C_USDT"))
    assert _legs_of_symbol(legs, "B_USDT") == ("macd", "tri")
    assert _legs_of_symbol(legs, "Z_USDT") == ()
