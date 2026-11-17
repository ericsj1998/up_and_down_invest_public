"""겹침 경보가 **계획 0 계약**인 몫에서 던지지 않는다 (1.48.0 배포 직후 실측 · 2026-11-17).

예산 x 배율이 계약 하나 값보다 작아 최소 한 계약으로 올려 산 몫(계획 배율 0.28 · 체결 1 계약)을
든 채 판이 다시 떴다. 겹침 경보(`_warn_if_stacked`)가 "체결 ÷ 계획" 을 0 으로 나눠 던졌고, 그
뒤에 있는 손절 걸기가 그 걸음에 안 돌았다(손절은 이미 걸려 있어 무방비는 없었다).

지키는 것:

- 계획이 0 으로 나와도 던지지 않는다 — 경보가 보호를 막으면 안 된다.
- 최소 한 계약으로 올려 산 것은 겹침이 아니다(경보 없음).
- 진짜 겹침(계획의 몇 배)은 그대로 경보한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from test_share_runner import share
from updown.orchestration.walkforward.live_runner import LiveRunner


class Log:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, name: str, **_: object) -> None:
        self.errors.append(name)


class Made:
    """`_warn_if_stacked` 가 만지는 것만 가진 러너 — 실제 메서드를 돌린다."""

    _warn_if_stacked = LiveRunner._warn_if_stacked  # pyright: ignore[reportPrivateUsage]

    def __init__(self, *, size_min: str) -> None:
        self.fund_ready = True
        self._session = SimpleNamespace(ledger=SimpleNamespace(sizing_base=Decimal("164.5")))
        self._log = Log()
        self.last_error = ""
        self.fired: list[tuple[str, str]] = []
        self._spec = {"quanto_multiplier": "1", "order_size_min": size_min}

    async def _contract_spec(self) -> dict[str, str]:
        return self._spec

    def _fired(self, name: str, detail: str) -> None:
        self.fired.append((name, detail))


def small() -> Any:
    """계약 하나 값(71.54)이 예산 x 배율(164.5 x 0.284 = 46.7)보다 큰 몫 — 계획은 0 계약이다."""
    return dc_replace(
        share("e7c1e5864721", stop="66.9", contracts=1, playbook="crash@0.1.0"),
        entry=Decimal("71.54"),
        leverage=Decimal("0.28424072"),
    )


def warn(made: Made, record: Any, filled: int) -> None:
    asyncio.run(cast("Any", made)._warn_if_stacked(record, filled))


def test_a_plan_of_zero_contracts_does_not_throw() -> None:
    """최소 주문 수량이 0 으로 읽히는 계약 — 계획 0 · 체결 1. 예전엔 0 으로 나눠 던졌다."""
    made = Made(size_min="0")
    warn(made, small(), 1)
    assert made.fired == [] and made._log.errors == [], "한 계약으로 올려 산 것은 겹침이 아니다"  # pyright: ignore[reportPrivateUsage]


def test_a_plan_below_the_minimum_is_still_quiet() -> None:
    made = Made(size_min="1")
    warn(made, small(), 1)
    assert made.fired == [] and made._log.errors == []  # pyright: ignore[reportPrivateUsage]


def test_a_real_stack_is_still_reported_even_when_the_plan_is_zero() -> None:
    made = Made(size_min="0")
    warn(made, small(), 5)
    assert made.fired and made.fired[0][0] == "stacked"
    assert made._log.errors == ["live_position_stacked"]  # pyright: ignore[reportPrivateUsage]
    assert "5.0배" in made.last_error
