"""삼각 탐지기 0.2 — 연구 `events_of` 의 두 규칙을 사슬로 (T378 · 2026-10-05).

판정 수식(`_candidates`)은 기존 시험이 지킨다. 여기서는 그것을 대본으로 바꿔 끼우고 **사슬**만 본다:

- 이탈 뒤 `rest_bars` 봉은 쉰다(연구 `rest_until = t + 24`).
- 이탈을 낸 첫 스윙은 다시 안 쓰고 더 작은 count 로 넘어간다(연구 `first in used: continue`).
- 살아 있는 삼각형(종가가 안)이면 더 작은 count 로 다시 잡지 않는다(연구 `break`).
- 손절이 진입가 너머인 이탈은 사슬을 소모하지 않는다(연구 `ok`).
- 끄면(0 · 0) 상태 없는 0.1 과 같다 · 설정은 0.2 로 켜져 있다.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import count
from typing import Any

import pytest

from test_private_strategy import triangle
from updown.analysis.detectors import private_strategy as tb
from updown.analysis.detectors.rules import load_rules

KEYS = count()


def tri(*, ok: bool = True) -> tb.Triangle:
    """숏 이탈 하나 — 손절(마지막 고점 + 0.2 ATR)이 진입가 위면 `ok`."""
    return tb.Triangle(
        side=-1,
        legs=3,
        first=0,
        upper_at_break=0.0,
        lower_at_break=0.0,
        last_high=1e9 if ok else 0.0,
        last_low=0.0,
        unit=1.0,
    )


def chain(window: list[Any], *, rest_bars: int = 24, once: bool = True) -> tb.Triangle | None:
    return tb.triangle_chain(
        window,
        key=("T378", next(KEYS)),
        swing_k=3,
        look=120,
        touch_atr=Decimal("0.3"),
        min_width_atr=Decimal(2),
        break_atr=Decimal("0.25"),
        where_min=Decimal("0.40"),
        where_max=Decimal("0.85"),
        sl_atr=Decimal("0.2"),
        rest_bars=rest_bars,
        once_per_first=once,
    )


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> dict[int, list[tuple[int, tb.Triangle | None]]]:
    """봉 위치 → `_candidates` 대본(없으면 빈 목록)."""
    plan: dict[int, list[tuple[int, tb.Triangle | None]]] = {}

    def fake(_swings: Any, _closes: Any, t: int, _unit: float, **_kw: Any) -> list[Any]:
        return list(plan.get(t, []))

    monkeypatch.setattr(tb, "_candidates", fake)
    return plan


class TestRest:
    def test_rest_blocks_the_next_break_within_24_bars(self, script: dict[int, Any]) -> None:
        bars = triangle(None)
        a, b, c = tri(), tri(), tri()
        script.update({40: [(10, a)], 50: [(20, b)], 70: [(30, c)]})
        assert chain(bars[:41]) is a
        assert chain(bars[:51]) is None, "40 + 24 안"
        assert chain(bars[:71]) is c, "64 지나면 다시"

    def test_without_rest_it_fires(self, script: dict[int, Any]) -> None:
        bars = triangle(None)
        b = tri()
        script.update({40: [(10, tri())], 50: [(20, b)]})
        assert chain(bars[:51], rest_bars=0, once=False) is b


class TestUsedFirst:
    def test_used_first_goes_to_a_smaller_count(self, script: dict[int, Any]) -> None:
        bars = triangle(None)
        big, small = tri(), tri()
        script.update({40: [(10, tri())], 70: [(10, big), (30, small)]})
        assert chain(bars[:71], rest_bars=0, once=True) is small
        assert chain(bars[:71], rest_bars=0, once=False) is big

    def test_a_live_triangle_stops_the_search(self, script: dict[int, Any]) -> None:
        bars = triangle(None)
        script.update({70: [(15, None), (30, tri())]})
        assert chain(bars[:71]) is None, "안이면 더 작은 count 로 다시 잡지 않는다"

    def test_bad_stop_does_not_use_the_first(self, script: dict[int, Any]) -> None:
        bars = triangle(None)
        good = tri()
        script.update({40: [(10, tri(ok=False))], 70: [(10, good)]})
        assert chain(bars[:41]) is None
        assert chain(bars[:71]) is good, "손절이 틀린 이탈은 첫 스윙을 쓰지 않았다"


class TestConfig:
    def test_the_rule_turns_the_chain_on(self) -> None:
        values = load_rules()["private_strategy"].params
        assert int(values["rest_bars"]) == 24
        assert int(values["once_per_first"]) == 1
        assert load_rules()["private_strategy"].version == "0.2" == tb.RULE_VERSION

    def test_chain_agrees_with_stateless_on_a_single_break(self) -> None:
        """기존 시험의 삼각형(이탈 하나) — 사슬도 같은 삼각형을 낸다."""
        window = triangle(tb.Decimal(90) + tb.Decimal("0.1") * 70 - tb.Decimal(3))
        alone = tb.closed_private_strategy(
            window,
            swing_k=3,
            look=120,
            touch_atr=Decimal("0.3"),
            min_width_atr=Decimal(2),
            break_atr=Decimal("0.25"),
            where_min=Decimal("0.40"),
            where_max=Decimal("0.85"),
        )
        chained = chain(window)
        assert alone is not None and chained is not None
        assert chained.first == alone.first and chained.side == alone.side == -1
