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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from updown.common import paths
from updown.orchestration.rebalancer.anchor import DNW_TYPES

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
    """스냅샷 파일 — 산출물 루트 아래 `equity/daily.jsonl`.

    Returns:
        `paths.under("equity", "daily.jsonl")` — 배포 볼륨과 로컬 모두 같은 규칙.
    """
    return paths.under("equity", "daily.jsonl")


def _dec(value: object) -> Decimal | None:
    """JSON 칸을 `Decimal` 로 — null 이거나 못 읽으면 None (0 으로 꾸미지 않는다 · 규칙 #8)."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _str(value: Decimal | None) -> str | None:
    """`_dec` 의 역 — JSON 에는 Decimal 을 문자열로 적는다 (float 오차를 안 남긴다)."""
    return None if value is None else str(value)


def to_row(point: EquityPoint) -> dict[str, Any]:
    """JSON 한 줄(또는 대시보드 한 항목) — Decimal 은 문자열, 없는 값은 null.

    Args:
        point: 스냅샷 한 점.

    Returns:
        `{at, total, available, locked, unrealized, vault, funds}` — `at` 은 UTC ISO.
    """
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
    """여러 점을 대시보드 JSON 목록으로.

    Args:
        points: 시각 순 점들.

    Returns:
        `to_row` 를 점마다 적용한 목록.
    """
    return [to_row(p) for p in points]


def _from_row(raw: dict[str, Any]) -> EquityPoint | None:
    """`to_row` 의 역 — 파일 한 줄을 점으로.

    시각이 없거나 깨진 줄은 점이 아니므로 None(호출처가 건너뛴다). 시각에 시간대가 없으면 UTC
    로 본다 — 적는 쪽이 늘 UTC ISO 로 적기 때문이다(규칙 #7). 금액 칸은 하나가 깨져도 나머지는
    살린다(`_dec` 가 None).

    Args:
        raw: JSON 한 줄.

    Returns:
        점. 시각을 못 읽으면 None.
    """
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
    """오늘(KST) 스냅샷이 이미 있나 — 하루 한 줄만 적는다.

    Args:
        points: 지금까지의 점들.
        now: 지금 (aware).

    Returns:
        KST 날짜가 오늘인 점이 하나라도 있으면 참.
    """
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


WALLET_POINTS = 120
"""기간별 지갑 그래프의 점 수 — 24시간이면 12분 · 30일이면 6시간 간격."""


@dataclass(frozen=True, slots=True)
class WalletPoint:
    """기간별 지갑 그래프 한 점 — 그 시각의 지갑 잔고와 **그때까지 넣은 돈**."""

    at: datetime
    balance: Decimal
    principal: Decimal


@dataclass(frozen=True, slots=True)
class WalletSeries:
    """기간별 지갑 그래프 — 넣은 돈 · 번 돈 · 잃은 돈을 한 그래프에.

    사용자 2026-09-27: *"내가 벌어서 얻은 수익과 내가 넣은 돈과, 잃은 돈이 그래프에서 한번에
    보여야 해"*.

    Attributes:
        points: 시각 순 점들 — 첫 점이 구간 시작, 마지막 점이 구간 끝.
        principal: 구간 끝까지 넣은 돈(입금에서 출금을 뺀 합).
        balance: 구간 끝 지갑 잔고.
        deposits: 구간 **안** 입출금 합 — 이 구간의 잔고 변화 중 번 돈이 아닌 몫.
        reached: 받은 장부가 구간 시작까지 닿았나. 거짓이면 첫 점은 받은 가장 옛 줄에서
            거꾸로 푼 값이다.
    """

    points: list[WalletPoint]
    principal: Decimal
    balance: Decimal
    deposits: Decimal
    reached: bool


def _when(raw: object) -> datetime | None:
    """장부 `time`(Gate 초 · 바이낸스 ms) → aware UTC. 못 읽으면 None."""
    try:
        value = float(str(raw))
    except ValueError:
        return None
    if value > 1e12:  # 밀리초
        value /= 1000
    return datetime.fromtimestamp(value, UTC)


def wallet_series(
    book: Sequence[Mapping[str, str]],
    deposits: Sequence[Mapping[str, str]],
    since: datetime,
    until: datetime,
    *,
    points: int = WALLET_POINTS,
) -> WalletSeries | None:
    """자금 원장 → 구간의 지갑 잔고 · 넣은 돈 시계열 (순수 · 2026-09-27).

    Args:
        book: 최근 자금 원장 줄들(`type` · `change` · `balance` · `time`) — 순서 무관.
            `balance` 는 그 변동 **뒤** 잔고다(Gate). 구간 앞의 줄이 섞여 있어야 구간 시작
            잔고를 안다.
        deposits: 입출금 줄들(전 기간) — 넣은 돈의 원천. `book` 과 겹쳐도 된다(여기서만 센다).
        since: 구간 시작(aware).
        until: 구간 끝(aware).
        points: 구간을 나눌 점 수. 점마다 **그 시각까지의 마지막 줄**의 잔고를 쓴다(계단).

    Returns:
        시계열. `balance` 를 읽을 줄이 하나도 없으면 None — 지어내지 않는다(규칙 #8).

    Note:
        🔴 **지갑 잔고 = 실현 기준**이다(미실현 제외 · 리포트 계좌 총액 카드와 같은 정의).
        입금은 잔고를 올리지만 번 돈이 아니므로 넣은 돈(`principal`)에도 같이 더한다 — 둘의
        차이가 번 돈(양수) · 잃은 돈(음수)이다. 넣은 돈은 거래소가 주는 입출금 기록의 합이라,
        기록이 거래소 보관 기간(Gate 약 180일) 밖이면 모자랄 수 있다.
    """
    rows: list[tuple[datetime, Decimal, Decimal]] = []
    for raw in book:
        at = _when(raw.get("time"))
        bal = _dec(raw.get("balance"))
        change = _dec(raw.get("change")) or Decimal(0)
        if at is None or bal is None:
            continue
        rows.append((at, change, bal))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    moves: list[tuple[datetime, Decimal]] = []
    for raw in deposits:
        at = _when(raw.get("time"))
        change = _dec(raw.get("change"))
        if at is not None and change is not None and str(raw.get("type", "dnw")) in DNW_TYPES:
            moves.append((at, change))
    moves.sort(key=lambda m: m[0])

    def principal_at(t: datetime) -> Decimal:
        return sum((c for at, c in moves if at <= t), Decimal(0))

    before = [r for r in rows if r[0] <= since]
    reached = bool(before)
    start = before[-1][2] if before else rows[0][2] - rows[0][1]
    edges = [since + (until - since) * k / points for k in range(points + 1)]
    out: list[WalletPoint] = []
    idx, bal = 0, start
    for edge in edges:
        while idx < len(rows) and rows[idx][0] <= edge:
            if rows[idx][0] > since:
                bal = rows[idx][2]
            idx += 1
        out.append(WalletPoint(at=edge, balance=bal, principal=principal_at(edge)))
    inside = sum((c for at, c in moves if since < at <= until), Decimal(0))
    last = out[-1]
    return WalletSeries(
        points=out,
        principal=last.principal,
        balance=last.balance,
        deposits=inside,
        reached=reached,
    )


def wallet_payload(series: WalletSeries | None) -> dict[str, Any] | None:
    """`wallet_series` → 대시보드 JSON — Decimal 은 문자열, 없으면 None.

    Args:
        series: 시계열.

    Returns:
        `{points: [{at, balance, principal}], principal, balance, earned, deposits, reached}`
        또는 None.
    """
    if series is None:
        return None
    return {
        "points": [
            {
                "at": p.at.astimezone(UTC).isoformat(),
                "balance": str(p.balance),
                "principal": str(p.principal),
            }
            for p in series.points
        ],
        "principal": str(series.principal),
        "balance": str(series.balance),
        "earned": str(series.balance - series.principal),
        "deposits": str(series.deposits),
        "reached": series.reached,
    }
