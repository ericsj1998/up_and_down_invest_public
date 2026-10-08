"""수집기(T283 호가 · 체결 · 통계)가 진입 순간에 본 것 — 로컬 `logs/orderflow` (T444)."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def orderflow_at(
    root: Path, market: str, symbol: str, at: datetime, tolerance_min: int = 2
) -> dict[str, Any]:
    """진입 시각(분 단위)에 가장 가까운 `book` · `trades` · `stats` 관측을 합쳐 준다.

    Args:
        root: `logs/orderflow`.
        market: `GATE`.
        symbol: `BTC_USDT`.
        at: 진입 시각(UTC).
        tolerance_min: 이 분 안의 관측만 받는다(수집기가 분당 1번이라 보통 0 ~ 1분).

    Returns:
        `{spread_bp, imb5, imb20, buy_ratio, open_interest_usd, lsr_taker, observed_at}` 중 있는 것.
        자료가 없으면 빈 dict —
        0 으로 꾸미지 않는다.
    """
    day = at.strftime("%Y-%m-%d")
    folder = root / market / symbol
    rows: list[dict[str, Any]] = []
    for name in (f"{day}.jsonl", f"{day}.jsonl.gz"):
        path = folder / name
        if not path.exists():
            continue
        text = (
            gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
            if name.endswith(".gz")
            else path.read_text(encoding="utf-8", errors="replace")
        )
        for line in text.splitlines():
            try:
                got = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(got, dict):
                rows.append(got)  # pyright: ignore[reportUnknownArgumentType]
        break
    if not rows:
        return {}
    best: dict[str, dict[str, Any]] = {}
    gap: dict[str, timedelta] = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row.get("ts", "")))
        except ValueError:
            continue
        kind = str(row.get("kind", ""))
        d = abs(ts - at)
        if d > timedelta(minutes=tolerance_min if kind != "stats" else 6):
            continue
        if kind not in gap or d < gap[kind]:
            gap[kind], best[kind] = d, row
    out: dict[str, Any] = {}
    book = best.get("book", {})
    for key in ("spread_bp", "imb5", "imb20"):
        if key in book:
            out[key] = book[key]
    if "buy_ratio" in best.get("trades", {}):
        out["buy_ratio"] = best["trades"]["buy_ratio"]
    stats = best.get("stats", {})
    for key in ("open_interest_usd", "lsr_taker"):
        if key in stats:
            out[key] = stats[key]
    if best:
        out["observed_at"] = min(best.values(), key=lambda r: str(r.get("ts", "")))["ts"]
    return out
