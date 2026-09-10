"""거시 지표 — 값 하나와 그 해석 (순수 · T262 · 사용자 요구 2026-09-10).

공포지수(VIX)·나스닥100 선물·환율·금리·물가처럼 **종목이 아닌** 시장 지표를 한 모양으로 담는다.
출처는 `marketdata/macro` 가 대고, 여기는 값의 뜻(구간·색·설명)만 안다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

VIX_BANDS: tuple[tuple[Decimal | None, str, str], ...] = (
    (Decimal(20), "안정", "calm"),
    (Decimal(30), "약한 공포", "fear"),
    (None, "강한 공포", "panic"),
)
"""VIX 구간 — `(상한(미만), 이름, 색조)`.

사용자 정의(2026-09-10): 20 미만 안정 · 20~29 약한 공포 · 30 이상 강한 공포.
"""

VIX_NOTE = (
    "공포지수가 높을수록 일시적인 강한 하락세가 발생하므로, 매수 기회일 수 있다. "
    "다만 지표는 분위기를 재는 것이지 방향을 맞히는 것이 아니다 — "
    "자리는 차트와 근거로 정한다."
)
"""VIX 카드 아래 설명 — 사용자 문장 + 규칙 #2(AI·지표는 근거, 결정은 사람)."""


def vix_band(value: Decimal) -> tuple[str, str]:
    """VIX 값 → (구간 이름, 색조).

    Args:
        value: VIX.

    Returns:
        `("안정", "calm")` · `("약한 공포", "fear")` · `("강한 공포", "panic")`.
    """
    for ceiling, label, tone in VIX_BANDS:
        if ceiling is None or value < ceiling:
            return label, tone
    return VIX_BANDS[-1][1], VIX_BANDS[-1][2]


def pct_change(last: Decimal, prev: Decimal | None) -> Decimal | None:
    """전일 대비 %.

    Args:
        last: 지금 값.
        prev: 기준 값.

    Returns:
        `(last/prev - 1) x 100`. 기준이 없거나 0 이면 None.
    """
    if prev is None or prev == 0:
        return None
    return (last / prev - 1) * 100


def cpi_yoy(points: Sequence[tuple[str, Decimal]]) -> tuple[str, Decimal, Decimal | None]:
    """CPI 지수 시계열 → (최신 기간, 최신 지수, 전년 동월 대비 %).

    Args:
        points: `("YYYY-MM", 지수)` 들 — 순서 무관 · 연간(M13) 행은 호출부가 뺀다.

    Returns:
        최신 기간과 지수, 그리고 12개월 전 같은 달이 있으면 전년 대비 %(없으면 None).

    Raises:
        ValueError: 비었다.
    """
    if not points:
        raise ValueError("CPI 시계열이 비었다")
    table = dict(points)
    latest = max(table)
    year, month = latest.split("-")
    prior = f"{int(year) - 1}-{month}"
    base = table.get(prior)
    return latest, table[latest], pct_change(table[latest], base)


@dataclass(frozen=True, slots=True)
class Indicator:
    """거시 지표 한 줄.

    Attributes:
        key: 짧은 열쇠(`vix` · `nq` · `usdkrw` · `effr` · `cpi` …).
        label: 사람 이름.
        value: 값.
        unit: 단위 표기(`pt` · `%` · `KRW` · `USD`).
        source: 출처 이름(화면·근거에 그대로).
        as_of: 값의 시각(UTC). 모르면 None.
        change_pct: 전일(전 값) 대비 %. 없으면 None.
        band: 구간 이름(VIX 만).
        tone: 색조(`calm` · `fear` · `panic`).
        note: 한 줄 설명(목표범위 · 지수값 등).
    """

    key: str
    label: str
    value: Decimal
    unit: str
    source: str
    as_of: datetime | None = None
    change_pct: Decimal | None = None
    band: str | None = None
    tone: str | None = None
    note: str = ""

    def as_json(self) -> dict[str, Any]:
        """화면·도구 모양.

        Returns:
            필드 그대로의 dict — 숫자는 문자열(정밀도 유지) · 시각은 ISO.
        """
        return {
            "key": self.key,
            "label": self.label,
            "value": str(self.value),
            "unit": self.unit,
            "source": self.source,
            "as_of": None if self.as_of is None else self.as_of.isoformat(),
            "change_pct": None if self.change_pct is None else str(self.change_pct),
            "band": self.band,
            "tone": self.tone,
            "note": self.note,
        }


__all__ = ["VIX_BANDS", "VIX_NOTE", "Indicator", "cpi_yoy", "pct_change", "vix_band"]
