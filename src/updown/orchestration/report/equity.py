"""자산 시계열 — 계좌 총액·펀드 잔고의 **일일 스냅샷**을 쌓고, 월별로 요약한다 (사용자 2026-09-07).

> *"지금 내 자산이 한 달 단위로 어떻게 바뀌었는지, 시계열 기반 꺾은선 그래프로."*

지금까지 이 저장소에는 계좌 총액의 **시간 축**이 없었다 — 펀드 파일은 마지막 값만 덮어쓰고, 원장의
청산 손익은 %의 합이라 금액 곡선을 못 만든다(구간 시작 자본을 모른다 · `dashboard.gain_curve` 주석).
그래서 여기서 하루 한 줄을 적는다. 첫 줄부터 쌓이므로 **배포 전 과거는 없다** — 지어내지 않는다.

- 저장: `logs/equity/daily.jsonl` (마운트된 볼륨 · 펀드 파일과 같은 자리). 한 줄 = 한 스냅샷.
  시각은 UTC (규칙 #7).
- 못 읽은 값은 `null` — 0 으로 꾸미지 않는다 (규칙 #8).
- 월별 요약 = **그 달의 마지막 스냅샷** (달 경계는 KST — 사람이 읽는 달이다).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from updown.common import paths

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """스냅샷 한 점 — 콘솔 계좌 카드와 같은 정의(총액 = 가용 + 포지션 증거금)."""

    at: datetime
    total: Decimal | None
    available: Decimal | None
    locked: Decimal | None
    unrealized: Decimal | None
    vault: Decimal | None
    funds: dict[str, Decimal] = field(default_factory=dict[str, Decimal])
    """펀드 id → 펀드 잔고(TWR 원장 equity)."""


def snapshot_path() -> Path:
    """스냅샷 파일 — 산출물 루트 아래 `equity/daily.jsonl`."""
    return paths.under("equity", "daily.jsonl")


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def to_row(point: EquityPoint) -> dict[str, Any]:
    """JSON 한 줄(또는 대시보드 한 항목) — Decimal 은 문자열, 없는 값은 null."""
    return {
        "at": point.at.astimezone(UTC).isoformat(),
        "total": _str(point.total),
        "available": _str(point.available),
        "locked": _str(point.locked),
        "unrealized": _str(point.unrealized),
        "vault": _str(point.vault),
        "funds": {k: str(v) for k, v in point.funds.items()},
    }


def to_rows(points: Iterable[EquityPoint]) -> list[dict[str, Any]]:
    """여러 점을 대시보드 JSON 목록으로."""
    return [to_row(p) for p in points]


def _from_row(raw: dict[str, Any]) -> EquityPoint | None:
    try:
        at = datetime.fromisoformat(str(raw["at"]))
    except (KeyError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    funds_raw = cast("dict[str, Any]", raw.get("funds") or {})
    funds: dict[str, Decimal] = {}
    for key, value in funds_raw.items():
        parsed = _dec(value)
        if parsed is not None:
            funds[str(key)] = parsed
    return EquityPoint(
        at=at,
        total=_dec(raw.get("total")),
        available=_dec(raw.get("available")),
        locked=_dec(raw.get("locked")),
        unrealized=_dec(raw.get("unrealized")),
        vault=_dec(raw.get("vault")),
        funds=funds,
    )


def record(point: EquityPoint, path: Path | None = None) -> None:
    """스냅샷 한 줄을 덧붙인다.

    Args:
        point: 적을 점.
        path: 파일. None 이면 `snapshot_path()`.
    """
    target = path or snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(to_row(point), ensure_ascii=False) + "\n")


def load(path: Path | None = None, *, since: datetime | None = None) -> list[EquityPoint]:
    """스냅샷 전부(또는 `since` 이후)를 시각 순으로.

    Args:
        path: 파일. None 이면 `snapshot_path()`.
        since: 이 시각 이후만.

    Returns:
        못 읽는 줄은 건너뛴 점들. 파일이 없으면 빈 목록.
    """
    target = path or snapshot_path()
    if not target.exists():
        return []
    points: list[EquityPoint] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw: object = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        point = _from_row(cast("dict[str, Any]", raw))
        if point is None or (since is not None and point.at < since):
            continue
        points.append(point)
    points.sort(key=lambda p: p.at)
    return points


def recorded_today(points: Sequence[EquityPoint], now: datetime) -> bool:
    """오늘(KST) 스냅샷이 이미 있나 — 하루 한 줄만 적는다."""
    today = now.astimezone(KST).date()
    return any(p.at.astimezone(KST).date() == today for p in points)


def monthly(points: Sequence[EquityPoint]) -> list[EquityPoint]:
    """달마다 **마지막** 스냅샷 하나 — 달 경계는 KST.

    Args:
        points: 시각 순 점들.

    Returns:
        달 순. 마지막 달은 진행 중인 달의 최신 점이다.
    """
    last: dict[tuple[int, int], EquityPoint] = {}
    for point in points:
        local = point.at.astimezone(KST)
        last[(local.year, local.month)] = point
    return [last[key] for key in sorted(last)]
