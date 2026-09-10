"""거시 지표 API (T262) — `GET /macro`. 화면 카드와 채팅 도구가 같은 함수를 부른다.

값은 60초 기억한다(야후·토스는 분 단위, 연준·BLS 는 일·월 단위라 그 이상은 낭비다).
실패한 지표는 `failures` 에 이유와 함께 남는다 — 못 띄우는 것을 조용히 빼지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from updown.common.domain.macro import VIX_BANDS, VIX_NOTE
from updown.marketdata.macro.adapter import KEYS
from updown.marketdata.provider import macro_adapter

router = APIRouter(prefix="/macro", tags=["macro"])

MACRO_TTL_S = 60.0
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = asyncio.Lock()


async def macro_snapshot(keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    """지표 묶음 — 화면과 채팅 도구의 공통 입구.

    Args:
        keys: 고를 열쇠들(`KEYS`). None 이면 전부.

    Returns:
        `{at, indicators: [...], failures: [{key, label, reason}], vix_bands, vix_note, keys}`.
    """
    cache_key = ",".join(sorted(keys)) if keys else "*"
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached is not None and now - cached[0] < MACRO_TTL_S:
        return cached[1]
    async with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < MACRO_TTL_S:
            return cached[1]
        adapter = macro_adapter()
        found, failures = await adapter.indicators(keys)
        body: dict[str, Any] = {
            "at": datetime.now(UTC).isoformat(),
            "indicators": [item.as_json() for item in found],
            "failures": failures,
            "vix_bands": [
                {"below": None if ceiling is None else str(ceiling), "label": label, "tone": tone}
                for ceiling, label, tone in VIX_BANDS
            ],
            "vix_note": VIX_NOTE,
            "keys": list(KEYS),
        }
        _CACHE[cache_key] = (time.monotonic(), body)
        return body


@router.get("")
async def macro() -> dict[str, Any]:
    """거시 지표 카드 — VIX(구간·색·설명)와 12종.

    나스닥100 선물 · S&P 500 · 미국 10년물 · 달러 인덱스 · 금 · WTI · 원달러 · 미국 기준금리 ·
    미국 CPI · 코스피 · 코스닥 · 한국 10년물.

    Returns:
        `macro_snapshot()` 모양. 읽기라 게스트도 본다(시장 공개 값).
    """
    return await macro_snapshot()


__all__ = ["macro_snapshot", "router"]
