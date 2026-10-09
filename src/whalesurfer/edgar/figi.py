"""OpenFIGI — CUSIP → 티커 (T442 D3 · 사용자 2026-10-08 "키 없이 쓰자").

13F 에는 티커가 없고 CUSIP 만 있다. OpenFIGI(블룸버그 · 무료)의 매핑 API 가 CUSIP 을 티커 ·
거래소 · 이름으로 돌려준다. 키 없이는 **분당 25 요청 · 요청당 10건**(키가 있으면 6초당 25 요청 ·
100건). 결과는 파일에 캐시해 같은 CUSIP 을 다시 묻지 않는다.
한 CUSIP 에 여러 상장(US 종합 · UA · UC …)이 오면 `exchCode == "US"`(미국 종합)를 먼저 고른다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from updown.common.http.outbound import Outbound, OutboundError
from updown.common.logging.setup import get_logger

_logger = get_logger("marketdata.figi")

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
JOBS_PER_REQUEST_NO_KEY = 10
JOBS_PER_REQUEST_KEY = 100
PAUSE_NO_KEY_S = 2.5
"""키 없이 분당 25 요청 → 2.4초 간격. 조금 여유를 둔다."""
PAUSE_KEY_S = 0.3
PREFERRED_EXCH = "US"


def pick(data: list[dict[str, Any]]) -> dict[str, Any] | None:
    """후보 중 미국 종합(`US`) 상장을 먼저, 없으면 첫 것."""
    for item in data:
        if item.get("exchCode") == PREFERRED_EXCH:
            return item
    return data[0] if data else None


class FigiClient:
    """CUSIP → 티커 매핑 + 파일 캐시."""

    def __init__(
        self, cache_path: Path, api_key: str | None = None, outbound: Outbound | None = None
    ) -> None:
        """만든다.

        Args:
            cache_path: 캐시 JSON(`{cusip: {ticker, exch, name, sector, at}}`). 없으면 만든다.
            api_key: OpenFIGI 키. None 이면 키 없이(느린 한도).
            outbound: 시험용 HTTP 층.
        """
        self._cache_path = cache_path
        self._key = api_key
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-OPENFIGI-APIKEY"] = api_key
        self._http = outbound or Outbound("OPENFIGI", timeout=30.0, headers=headers)
        self._cache: dict[str, dict[str, Any]] = {}
        if cache_path.exists():
            self._cache = cast(
                "dict[str, dict[str, Any]]", json.loads(cache_path.read_text(encoding="utf-8"))
            )

    @property
    def cache(self) -> dict[str, dict[str, Any]]:
        """지금까지 안 것(읽기 전용으로 쓴다)."""
        return self._cache

    def _save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")

    async def map_cusips(
        self, cusips: Iterable[str], *, refresh_misses: bool = False
    ) -> dict[str, dict[str, Any]]:
        """CUSIP 들을 티커로 — 캐시에 없는 것만 묻는다.

        Args:
            cusips: 9자리 CUSIP.
            refresh_misses: True 면 예전에 "없음" 으로 적힌 것도 다시 묻는다.

        Returns:
            `{cusip: {"ticker": str|None, "exch", "name", "sector", "at"}}` — 물은 것 전부
            (없으면 ticker None).
        """
        wanted = sorted({c.strip().upper() for c in cusips if c and c.strip()})
        todo = [
            c
            for c in wanted
            if c not in self._cache or (refresh_misses and self._cache[c].get("ticker") is None)
        ]
        per = JOBS_PER_REQUEST_KEY if self._key else JOBS_PER_REQUEST_NO_KEY
        pause = PAUSE_KEY_S if self._key else PAUSE_NO_KEY_S
        for start in range(0, len(todo), per):
            chunk = todo[start : start + per]
            jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
            try:
                response = await self._http.request("POST", MAPPING_URL, json=jobs)
                body = (
                    cast("list[dict[str, Any]]", response.json())
                    if response.status_code == 200
                    else None
                )
                if body is None:
                    raise ValueError(f"OPENFIGI {response.status_code}")
            except (OutboundError, ValueError) as exc:
                _logger.warning(
                    "figi_batch_failed", payload={"n": len(chunk), "detail": str(exc)[:120]}
                )
                await asyncio.sleep(pause * 4)
                continue
            now = datetime.now(UTC).isoformat()
            for cusip, item in zip(chunk, body, strict=False):
                best = pick(cast("list[dict[str, Any]]", item.get("data") or []))
                self._cache[cusip] = {
                    "ticker": (best or {}).get("ticker"),
                    "exch": (best or {}).get("exchCode"),
                    "name": (best or {}).get("name"),
                    "sector": (best or {}).get("marketSector"),
                    "at": now,
                }
            self._save()
            if start + per < len(todo):
                await asyncio.sleep(pause)
        return {c: self._cache[c] for c in wanted if c in self._cache}


__all__ = ["MAPPING_URL", "FigiClient", "pick"]
