"""봉 보관 정책 — 무엇을 언제까지 남길지 (T278 · 2026-09-12).

**계획만 만들고 실행하지 않는다.** `partitions.py` 와 같은 규칙이다 — 순수 함수로 두면 DB 없이
시험할 수 있고, 실행하는 쪽(운영 스크립트)이 하나다.

## 🔴 파티션을 떼는 방법은 못 쓴다

봉 표는 **`ts` 기준** 월 파티션이다(`RANGE (ts)`). 한 달 파티션 안에 5분봉부터 일봉까지 **모든 축이
섞여** 있다 — 2026-07 파티션 실측: 5m 498,095 · 15m 54,679 · 1h 13,734 · 1d 2,645 · 4h 1,116.

그래서 `DROP` 으로 옛 달을 떼면 **일봉까지 같이 사라진다.** 일봉은 저평가 순위·장투·재무가 읽고
가장 오래 쓰이는 것이라 지우면 안 된다. 남는 길은 **축을 지정한 `DELETE`** 뿐이고, PostgreSQL 이
`ts` 조건으로 옛 파티션만 훑는다.

(처음에는 "월 파티션이라 DROP 한 번이면 된다" 고 적었다가 파티션 키를 확인하고 바로잡았다.)

## 무엇이 안전을 지키나

`never_delete` 에 적힌 축은 `keep_days` 에 들어와도 **계획에서 빠진다.** 설정을 잘못 고쳐도 일봉이
지워지지 않게 하는 자물쇠이고, 시험이 그것을 잠근다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from updown.common.domain.instrument import Timeframe

CONFIG = Path(__file__).resolve().parents[4] / "config" / "retention.yml"
"""정책 파일 — 임계값은 코드가 아니라 설정에 둔다 (CLAUDE.md §1)."""


@dataclass(frozen=True, slots=True)
class Sweep:
    """한 축을 어디까지 지울지.

    Attributes:
        timeframe: 대상 축.
        keep_days: 남길 일수.
        cutoff: 이 시각 **미만**의 봉이 대상이다 (UTC).
    """

    timeframe: Timeframe
    keep_days: int
    cutoff: datetime


@dataclass(frozen=True, slots=True)
class Policy:
    """보관 정책 한 벌.

    Attributes:
        keep_days: 축 → 남길 일수.
        never_delete: 어떤 경우에도 지우지 않는 축.
    """

    keep_days: dict[Timeframe, int]
    never_delete: frozenset[Timeframe]


def load_policy(path: Path | None = None) -> Policy:
    """`config/retention.yml` 을 읽는다.

    Args:
        path: 설정 파일. None 이면 저장소의 것.

    Returns:
        정책.

    Raises:
        ValueError: 모르는 축 이름이거나 남길 일수가 양수가 아니면 — 조용히 넘기면 엉뚱한 것이
            지워진다 (규칙 #8).
    """
    target = path or CONFIG
    raw = cast("dict[str, Any]", yaml.safe_load(target.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("candles") or {})
    never = frozenset(
        _frame(str(name)) for name in cast("list[Any]", body.get("never_delete") or [])
    )
    keep: dict[Timeframe, int] = {}
    for name, days in cast("dict[str, Any]", body.get("keep_days") or {}).items():
        frame = _frame(str(name))
        count = int(days)
        if count <= 0:
            raise ValueError(f"{name} 의 남길 일수가 양수가 아니다: {days}")
        keep[frame] = count
    return Policy(keep_days=keep, never_delete=never)


def _frame(name: str) -> Timeframe:
    """축 이름을 열거형으로. 모르면 예외 — 오타가 조용히 통과하면 안 된다."""
    try:
        return Timeframe(name)
    except ValueError as exc:
        known = ", ".join(item.value for item in Timeframe)
        raise ValueError(f"모르는 시간축이다: {name} (있는 것: {known})") from exc


def plan(policy: Policy, now: datetime | None = None) -> list[Sweep]:
    """지울 대상을 축마다 하나씩 만든다 (순수).

    Args:
        policy: 보관 정책.
        now: 기준 시각. None 이면 지금 (UTC).

    Returns:
        축별 `Sweep`. **`never_delete` 축은 들어가지 않는다.** 짧게 남기는 축이 앞에 온다.

    Note:
        🔴 안전장치가 여기 있다 — 설정에서 `keep_days` 에 일봉을 적어도 `never_delete` 가 이기고
        계획에서 빠진다. 정책 파일을 잘못 고쳐도 일봉이 사라지지 않는다.
    """
    at = now or datetime.now(UTC)
    out = [
        Sweep(timeframe=frame, keep_days=days, cutoff=at - timedelta(days=days))
        for frame, days in policy.keep_days.items()
        if frame not in policy.never_delete
    ]
    return sorted(out, key=lambda item: item.keep_days)


def delete_sql(table: str = "candles") -> str:
    """한 축을 지우는 문 — 값은 바인드로 넘긴다.

    Args:
        table: 부모 표 이름.

    Returns:
        `:frame` · `:cutoff` 두 자리를 쓰는 DELETE 문. 문자열을 이어 붙이지 않는다(§10 보안).

    Note:
        `ts <` 조건이 있어 PostgreSQL 이 **옛 파티션만** 훑는다. 파티션을 떼는 것이 아니므로
        공간은 OS 로 안 돌아가고 그 표가 다시 쓴다 — 표가 더 자라지 않게 하는 것이 목적이다.
    """
    return f"DELETE FROM {table} WHERE timeframe = :frame AND ts < :cutoff"


def count_sql(table: str = "candles") -> str:
    """지울 행 수를 미리 세는 문 — 같은 조건.

    Args:
        table: 부모 표 이름.

    Returns:
        `:frame` · `:cutoff` 를 쓰는 COUNT 문.
    """
    return f"SELECT count(*) FROM {table} WHERE timeframe = :frame AND ts < :cutoff"


__all__ = ["CONFIG", "Policy", "Sweep", "count_sql", "delete_sql", "load_policy", "plan"]
