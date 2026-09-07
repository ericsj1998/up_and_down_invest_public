"""펀딩(포지션 유지비) — 정산 경계와 거래소 정산 기록의 귀속 (T226 · 사용자 2026-09-07).

무기한 선물은 8시간마다(00·08·16 UTC) 펀딩을 정산한다. 롱은 요율이 양수면 내고, 숏은 받는다.
지금까지 실계좌·페이퍼·API 백테스트가 쓰는 원장은 이것을 몰랐다 — 보유가 길어도 비용이 늘지 않았다.

두 경로가 있고 둘 다 **순수 함수**로 두어 시험한다:
    · 모형 — 봉이 정산 경계를 지날 때 요율을 매매에 더한다 (`settlement_boundaries`).
      백테스트·페이퍼.
    · 실측 — 거래소 자금 원장(`account_book`)의 `fund`/`FUNDING_FEE` 행을 열린 매매에 붙인다
      (`attribute_funding`). 라이브.

⛔ 경계 규칙은 발굴 스캔(`discovery/costs.Funding`)과 같다: **진입 < 정산 <= 청산**.
비례식(보유시간/8h)은 단타에서 틀린다 — 7시간 55분을 들어도 경계를 안 지났으면 0 이고,
10분을 들어도 지났으면 한 번 낸다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

SETTLEMENT_HOURS = (0, 8, 16)
"""정산 시각 (UTC). Gate·Binance USDT 무기한 공통."""

FUNDING_KINDS = frozenset({"fund", "FUNDING_FEE"})
"""자금 원장의 펀딩 행 `type` — Gate `fund` · Binance `FUNDING_FEE`."""


def settlement_boundaries(previous: datetime, now: datetime) -> list[datetime]:
    """`previous < 경계 <= now` 인 정산 시각들 — 봉 하나가 지나며 넘은 경계.

    Args:
        previous: 직전 봉의 시각(UTC aware).
        now: 이번 봉의 시각.

    Returns:
        시각 순. 같은 시각이거나 되돌아가면 빈 목록.
    """
    if now <= previous:
        return []
    start = previous.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end = now.astimezone(UTC)
    found: list[datetime] = []
    cursor = start
    while cursor <= end:
        if cursor.hour in SETTLEMENT_HOURS and previous < cursor <= now:
            found.append(cursor)
        cursor += timedelta(hours=1)
    return found


@dataclass(frozen=True, slots=True)
class FundingRow:
    """거래소 자금 원장의 펀딩 한 줄.

    Attributes:
        at: 정산 시각 (UTC).
        change: 잔고 변화 (USDT · 음수 = 냈다).
        key: 중복 방지 열쇠 (`시각:변화`).
    """

    at: datetime
    change: Decimal
    key: str


def _same_symbol(row: dict[str, Any], symbol: str) -> bool:
    """`contract`(Binance) 또는 `text`(Gate · "NEAR_USDT:…") 가 이 종목인가 — 밑줄은 무시."""
    want = symbol.replace("_", "").upper()
    contract = str(row.get("contract", "") or "").replace("_", "").upper()
    if contract:
        return contract == want
    text = str(row.get("text", "") or "").replace("_", "").upper()
    return want in text


def funding_rows(rows: Iterable[dict[str, Any]], *, symbol: str) -> list[FundingRow]:
    """자금 원장 행들 중 **이 종목의 펀딩 행**만 골라 시각 순으로.

    Args:
        rows: `account_book` 행들 — `{type, change, time, text|contract}`. `time` 은 초 또는 ms.
        symbol: 종목 (`NEAR_USDT`).

    Returns:
        읽을 수 없는 행은 건너뛴다.
    """
    out: list[FundingRow] = []
    for row in rows:
        if str(row.get("type", "")) not in FUNDING_KINDS or not _same_symbol(row, symbol):
            continue
        try:
            stamp = float(str(row.get("time", "")))
            change = Decimal(str(row.get("change", "0")))
        except (ValueError, InvalidOperation):
            continue
        at = datetime.fromtimestamp(stamp / 1000 if stamp > 1e12 else stamp, UTC)
        out.append(FundingRow(at=at, change=change, key=f"{int(at.timestamp())}:{change}"))
    out.sort(key=lambda item: item.at)
    return out


def attribute_funding(
    rows: Sequence[dict[str, Any]],
    *,
    symbol: str,
    opened_at: datetime,
    closed_at: datetime | None,
    seen: set[str],
) -> tuple[list[FundingRow], set[str]]:
    """열린(또는 닫힌) 매매 구간 안의 **아직 안 붙인** 펀딩 행들.

    Args:
        rows: 자금 원장 행들.
        symbol: 종목.
        opened_at: 매매가 열린 시각.
        closed_at: 닫힌 시각. None 이면 지금까지.
        seen: 이미 붙인 행 열쇠들 — 같은 정산을 두 번 세지 않는다.

    Returns:
        `(새 행들, 갱신된 seen)`. 구간 규칙은 `opened_at < 정산 <= closed_at`.
    """
    fresh: list[FundingRow] = []
    next_seen = set(seen)
    for row in funding_rows(rows, symbol=symbol):
        if row.key in next_seen:
            continue
        if row.at <= opened_at or (closed_at is not None and row.at > closed_at):
            continue
        next_seen.add(row.key)
        fresh.append(row)
    return fresh, next_seen
