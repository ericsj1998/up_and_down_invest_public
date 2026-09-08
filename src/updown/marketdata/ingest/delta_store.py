"""델타 jsonl 파일을 읽어 `BarDelta` 로 되돌린다 (T28).

`collect_gate_delta.py`(실시간)와 `backfill_gate_delta.py`(과거)가 쓴 것을 읽는다 —
둘은 같은 형식이라 읽는 쪽이 하나다:

    logs/delta/BTC_USDT_15m_2026-08-22.jsonl    {"ts","buy","sell","trades"}

🔴 **적재된 KRW 델타(업비트)는 안 읽는다.** 그것은 현물이고 우리는 Gate 선물에서
주문한다 — *"어느 거래소의 값인지가 곧 그 값의 뜻이다."* 심볼로 가른다 (`BTC_USDT`
는 Gate, `KRW-BTC` 는 업비트).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from updown.common.domain.instrument import Timeframe
from updown.common.domain.trade_tick import BarDelta
from updown.common.logging.setup import get_logger
from updown.common.paths import under

if TYPE_CHECKING:
    from updown.common.domain.instrument import Instrument


def _parse_utc(raw: str) -> datetime:
    """저장한 ISO 시각을 UTC aware 로 — 우리가 쓴 값은 `+00:00` 을 포함한다."""
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


_logger = get_logger("marketdata.ingest.delta_store")


def load_bar_deltas(
    instrument: Instrument,
    timeframe: Timeframe = Timeframe.M15,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[BarDelta]:
    """종목의 봉 델타를 시각 오름차순으로 읽는다.

    Args:
        instrument: 대상 종목. 심볼로 파일 이름을 짓는다.
        timeframe: 시간축 (기본 15m — 델타는 이 축만 쌓는다).
        start: 이 시각 이후 봉만 (포함). None 이면 처음부터.
        end: 이 시각 이전 봉만 (미포함). None 이면 끝까지.

    Returns:
        시각 오름차순 `BarDelta`. 파일이 없으면 빈 목록 — 없는 것을 0 으로 꾸미지
        않는다. 부르는 쪽(`CvdSeries`)이 봉과 맞춰 구멍을 None 으로 둔다.

    Note:
        ⚠️ 하루 한 파일이라 날짜 정렬로 읽되, 파일 안이 이미 오름차순이라 이어 붙이면
        전체가 오름차순이다. 같은 봉이 두 파일(백필+실시간 경계)에 겹치면 **뒤에 읽은
        것으로 덮지 않고** 첫 값만 쓴다 — 실시간이 더 정확하지만 경계 한 봉이라 무해하고,
        중복 판정을 단순화한다.
    """
    root = under("delta")
    if not root.exists():
        return []
    prefix = f"{instrument.symbol}_{timeframe.value}_"
    seen: dict[datetime, BarDelta] = {}
    for path in sorted(root.glob(f"{prefix}*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = _parse_utc(str(row["ts"]))
                buy = Decimal(str(row["buy"]))
                sell = Decimal(str(row["sell"]))
                trades = int(row["trades"])
            except (json.JSONDecodeError, KeyError, ValueError, ArithmeticError) as exc:
                _logger.warning(
                    "delta_row_unreadable",
                    payload={"file": path.name, "error": str(exc)[:120]},
                )
                continue
            if start is not None and ts < start:
                continue
            if end is not None and ts >= end:
                continue
            if ts in seen:
                continue
            seen[ts] = BarDelta(
                instrument=instrument,
                timeframe=timeframe,
                ts=ts,
                buy_volume=buy,
                sell_volume=sell,
                trades=trades,
            )
    return [seen[ts] for ts in sorted(seen)]
