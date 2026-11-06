"""가벼운 첫 화면 재료 — 계좌 · 자본금 · 손익 · 열린 포지션만 (사용자 2026-11-06).

화면 폴링이 서버 CPU 의 큰 몫이라, 처음 뜨는 화면은 거래소를 거의 묻지 않고 꼭 볼 것만 낸다.
상세는 사람이 고를 때만 콘솔로 간다. 판단은 없다 — 받은 값을 모아 적을 뿐이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from updown.orchestration.report.performance import Performance


def _dec(value: object) -> Decimal | None:
    """금액 문자열 → Decimal. 못 읽으면 None(0 으로 꾸미지 않는다 · 규칙 #8)."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def lite_payload(
    *,
    at: datetime,
    available: Decimal | None,
    positions: Sequence[Mapping[str, Any]] | None,
    capital_moves: Sequence[Mapping[str, Any]] | None,
    realized_24h: Decimal | None,
    runs: int,
    leader: bool | None,
    run_of: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """첫 화면 한 덩어리를 만든다.

    Args:
        at: 만든 시각(aware).
        available: 쓸 수 있는 돈. 못 읽으면 None.
        positions: 열린 포지션 줄들(`symbol` · `size` · `margin` · `unrealised_pnl`).
            못 읽으면 None.
        capital_moves: 입출금 줄들(`change`). 못 읽으면 None.
        realized_24h: 최근 24 시간 순 실현 손익. 못 읽으면 None.
        runs: 살아 있는 판 수.
        leader: 이 프로세스가 거래 리더인가. 모르면 None.
        run_of: 종목 → 판 id — 포지션을 눌러 그 판 화면으로 간다. 판이 없는 다리(나스닥)는 빈 값.

    Returns:
        `{at, total, unrealized, capital, earned, realized_24h, runs, leader, positions}`.
        재료가 빠진 칸은 None 이다.

    Note:
        🔴 `earned` 는 입출금을 뺀 값이다 — 입금은 손익이 아니다. 입출금을 못 읽었으면 지갑 전체가
        번 돈으로 보이므로 None 으로 둔다.
    """
    rows: list[dict[str, Any]] = []
    locked: Decimal | None = None
    unrealized: Decimal | None = None
    if positions is not None:
        locked, unrealized = Decimal(0), Decimal(0)
        for item in positions:
            size = _dec(item.get("size")) or Decimal(0)
            if size == 0:
                continue
            margin = _dec(item.get("margin")) or Decimal(0)
            pnl = _dec(item.get("unrealised_pnl")) or Decimal(0)
            locked += margin
            unrealized += pnl
            rows.append(
                {
                    "symbol": str(item.get("symbol", "")),
                    "run": (run_of or {}).get(str(item.get("symbol", "")), ""),
                    "side": "long" if size > 0 else "short",
                    "unrealized": str(pnl),
                    "pct": None if margin <= 0 else str(pnl / margin * Decimal(100)),
                }
            )
        rows.sort(key=lambda r: Decimal(r["unrealized"]))
    total = None if available is None or locked is None else available + locked
    capital: Decimal | None = None
    if capital_moves is not None:
        capital = sum((_dec(row.get("change")) or Decimal(0) for row in capital_moves), Decimal(0))
    earned = None if total is None or capital is None else total - capital
    return {
        "at": at.isoformat(),
        "total": _text(total),
        "unrealized": _text(unrealized),
        "capital": _text(capital),
        "earned": _text(earned),
        "realized_24h": _text(realized_24h),
        "runs": runs,
        "leader": leader,
        "positions": rows,
    }


def brief_payload(perf: Performance, hours: int) -> dict[str, Any]:
    """첫 화면 약식 리포트 — 전체 리포트 맨 위 숫자와 같은 재료에서 꼭 볼 칸만 고른다.

    Args:
        perf: 원장 · 거래소 집계(전체 리포트 · 메일과 같은 것).
        hours: 고른 기간(시간).

    Returns:
        `{since, until, clipped, net, pnl, fees, funding, trades, longs, shorts, wins, win_rate,
        mean_rr, max_drawdown_pct, diverged, note}`. 거래소를 못 읽으면 금액 칸은 None.

    Note:
        `clipped` 는 손익 시작점 때문에 구간이 고른 기간보다 짧아졌다는 뜻이다. 표시 없이 두면
        "30 일" 칸의 몇 시간치 숫자를 30 일 성과로 읽게 된다.
    """
    led, exc = perf.ledger, perf.exchange
    return {
        "since": perf.window.since.isoformat(),
        "until": perf.window.until.isoformat(),
        "clipped": perf.window.until - perf.window.since < timedelta(hours=hours),
        "net": None if exc is None else str(exc.net),
        "pnl": None if exc is None else str(exc.pnl),
        "fees": None if exc is None else str(exc.fees),
        "funding": None if exc is None else str(exc.funding),
        "trades": led.trades,
        "longs": led.longs,
        "shorts": led.shorts,
        "wins": led.wins,
        "win_rate": _text(led.win_rate),
        "mean_rr": _text(led.mean_rr),
        "max_drawdown_pct": str(led.max_drawdown_pct),
        "diverged": perf.diverged,
        "note": perf.note,
    }
