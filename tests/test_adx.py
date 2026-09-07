# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
# pyright: reportPrivateImportUsage=false, reportCallIssue=false, reportArgumentType=false
#
# pandas-ta 는 타입 스텁이 없는 **대조 전용 dev 의존성**이다 (운영 코드는 import 안 함).
"""ADX(14) 자체 구현 — pandas-ta 대조 + 기본 성질 (T59 · 규칙 #9)."""

from __future__ import annotations

import random
from decimal import Decimal

import pandas as pd
import pandas_ta as pta
import pytest

from updown.analysis.indicators.adx import adx
from updown.analysis.indicators.series import SeriesError


def _series(n: int = 300) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    random.seed(3)
    h: list[Decimal] = []
    low: list[Decimal] = []
    c: list[Decimal] = []
    p = 100.0
    for _ in range(n):
        p += random.uniform(-2, 2.3)
        h.append(Decimal(str(p + random.uniform(0.2, 2))))
        low.append(Decimal(str(p - random.uniform(0.2, 2))))
        c.append(Decimal(str(p)))
    return h, low, c


def _floats(xs: list[Decimal]) -> list[float]:
    return [float(x) for x in xs]


def test_matches_pandas_ta_on_converged_tail() -> None:
    h, low, c = _series()
    ours = adx(h, low, c, 14)
    ref = pta.adx(pd.Series(_floats(h)), pd.Series(_floats(low)), pd.Series(_floats(c)), length=14)
    ref_adx = ref["ADX_14"].tolist()
    for i in range(len(c) - 50, len(c)):
        mine = ours[i]
        assert mine is not None and ref_adx[i] == ref_adx[i]
        assert abs(float(mine) - ref_adx[i]) < 0.05


def test_warmup_is_none_then_values() -> None:
    h, low, c = _series(80)
    out = adx(h, low, c, 14)
    assert out[0] is None and out[20] is None, "이중 평활이라 ~2*period 까지 None"
    assert any(v is not None for v in out), "충분히 길면 값이 나와야 한다"


def test_range_0_to_100() -> None:
    h, low, c = _series()
    vals = [float(v) for v in adx(h, low, c, 14) if v is not None]
    assert vals and all(0 <= v <= 100 for v in vals)


def test_length_mismatch_raises() -> None:
    with pytest.raises(SeriesError):
        adx([Decimal(1), Decimal(2)], [Decimal(1)], [Decimal(1), Decimal(2)], 14)
