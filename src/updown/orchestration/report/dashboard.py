"""리포트 **대시보드** 재료 — 메일과 같은 숫자를 JSON 으로 (T220 2단계 · 2026-09-05).

사용자: *"리포트도 해당 디자인에 맞춰서 — 딱 한 화면에 대시보드 형태로."*

🔴 **계산을 새로 만들지 않는다.** 원장 집계(`Performance`)·계좌 스냅샷(`AccountSummary`)·
판 제목줄(`meta_for`)은 메일이 쓰는 그 함수들이다 — 이 모듈은 그것들을 **직렬화**하고,
누적 곡선 하나만 더 뽑는다 (T35 원칙: 미리보기·발송·화면이 같은 길).

⚠️ 못 읽은 값은 `None` 으로 나간다 — 화면이 "—" 로 그린다. 0 으로 꾸미지 않는다 (규칙 #8).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from updown.orchestration.report import equity as equity_mod
from updown.orchestration.report.funds import FundSnapshot
from updown.orchestration.report.html import AccountSummary
from updown.orchestration.report.performance import Performance
from updown.orchestration.walkforward.ledger import TradeRecord


@dataclass(frozen=True, slots=True)
class RunRow:
    """대시보드 판 표 한 줄 — 메일 섹션(`RunSection`)의 제목줄 수치 + **판 키**(차트 열기 링크)."""

    key: str
    symbol: str
    playbook: str
    fund_label: str | None
    gain_pct: Decimal | None
    takes: int
    stops: int
    trades: int


@dataclass(frozen=True, slots=True)
class FundRow:
    """대시보드 펀드 카드 한 장 — 계좌와 판 사이의 중간 층 (사용자 2026-09-07)."""

    fund_id: str
    label: str
    market: str
    playbook: str
    balance: Decimal
    twr_pct: Decimal
    drawdown_pct: Decimal
    max_drawdown_pct: Decimal
    money_gain: Decimal
    symbols: tuple[str, ...]
    run_keys: tuple[str, ...]


def fund_rows(snapshots: Sequence[FundSnapshot]) -> list[FundRow]:
    """펀드 파일 스냅샷 → 대시보드 행 — 메일 카드(`compose.fund_sections`)와 같은 원천·산식.

    Args:
        snapshots: `funds.load_snapshots()` 가 읽은 펀드들.

    Returns:
        펀드마다 한 행 (입력 순서 그대로).
    """
    return [
        FundRow(
            fund_id=s.fund_id,
            label=s.label,
            market=s.market,
            playbook=s.playbook,
            balance=s.balance,
            twr_pct=s.twr_pct,
            drawdown_pct=s.drawdown_pct,
            max_drawdown_pct=s.max_drawdown_pct,
            money_gain=s.money_gain,
            symbols=s.symbols,
            run_keys=tuple(s.run_keys.values()),
        )
        for s in snapshots
    ]


@dataclass(frozen=True, slots=True)
class CurvePoint:
    """누적 손익률 경로의 한 점 — 청산 시각 순."""

    at: datetime
    symbol: str
    gain_pct: Decimal
    cum_pct: Decimal


def gain_curve(
    records: list[tuple[str, TradeRecord]], symbol_of: Mapping[str, str] | None = None
) -> list[CurvePoint]:
    """청산된 매매의 손익률을 **청산 시각 순으로 누적**한다.

    Args:
        records: `(판 키, 매매 기록)` — `load_closed_trades` 가 준 것.
        symbol_of: 판 키 → 종목. 원장 기록은 종목을 안 들고 있어(판 소속) 여기서 붙인다.
            없으면 빈 문자열.

    Returns:
        누적 곡선. 청산 시각이나 손익률이 없는 기록은 뺀다 (지어내지 않는다).

    Note:
        `LedgerSummary.gain_sum_pct` 와 같은 **합** 기준이다 (복리가 아니다). 구간 시작 시점의
        자본을 모르므로 금액 곡선은 그리지 않는다 — 24h 전 판 자본은 리플레이가 있어야 안다
        (compose.py 의 `equity_before=None`).
    """
    closed = [
        (r.closed_at, r.gain_pct, key, r)
        for key, r in records
        if r.closed_at is not None and r.gain_pct is not None
    ]
    closed.sort(key=lambda item: item[0])
    points: list[CurvePoint] = []
    total = Decimal(0)
    names = symbol_of or {}
    for at, gain, key, _record in closed:
        assert at is not None and gain is not None  # 필터가 보장한다 — 타입 좁히기
        total += gain
        points.append(CurvePoint(at=at, symbol=names.get(key, ""), gain_pct=gain, cum_pct=total))
    return points


def _dec(value: Decimal | None) -> str | None:
    """Decimal 은 문자열로 — JSON 부동소수로 바꾸지 않는다 (돈이다)."""
    return None if value is None else str(value)


def payload(
    perf: Performance,
    account: AccountSummary | None,
    rows: list[RunRow],
    curve: list[CurvePoint],
    *,
    configured: bool,
    default_to: list[str],
    funds: Sequence[FundRow] = (),
    equity: Sequence[equity_mod.EquityPoint] = (),
) -> dict[str, Any]:
    """대시보드 JSON 한 벌.

    Args:
        perf: 원장·거래소 집계 (메일과 같은 것).
        account: 계좌 스냅샷 — 살아 있는 러너가 없으면 None.
        rows: 판 표.
        curve: 누적 곡선.
        configured: SMTP 설정이 다 있나 (보내기 단추가 살아나는 조건).
        default_to: 기본 수신자.
        funds: 펀드 카드들 — 계좌와 판 사이 (사용자 2026-09-07).
        equity: 자산 시계열(월별 요약) — 비면 화면이 "쌓이는 중" 을 적는다.

    Returns:
        화면이 그대로 그리는 JSON — 구간 · 원장 · 거래소 · 계좌 · 판 표 · 곡선 · 발송 설정.
    """
    led = perf.ledger
    ex = perf.exchange
    return {
        "window": {"since": perf.window.since.isoformat(), "until": perf.window.until.isoformat()},
        "ledger": {
            "trades": led.trades,
            "longs": led.longs,
            "shorts": led.shorts,
            "wins": led.wins,
            "win_rate": _dec(led.win_rate),
            "gain_sum_pct": _dec(led.gain_sum_pct),
            "mean_rr": _dec(led.mean_rr),
            "max_drawdown_pct": _dec(led.max_drawdown_pct),
            "by_outcome": dict(led.by_outcome),
            "by_playbook": dict(led.by_playbook),
            "by_symbol": dict(led.by_symbol),
        },
        "exchange": None
        if ex is None
        else {
            "pnl": _dec(ex.pnl),
            "fees": _dec(ex.fees),
            "funding": _dec(ex.funding),
            "rows": ex.rows,
        },
        "diverged": perf.diverged,
        "note": perf.note,
        "account": None
        if account is None
        else {
            "total": _dec(account.total),
            "available": _dec(account.available),
            "locked": _dec(account.locked),
            "realized": _dec(account.realized),
            "unrealized": _dec(account.unrealized),
            "vault": _dec(account.vault),
            "runs": account.runs,
        },
        "funds": [
            {
                "fund_id": f.fund_id,
                "label": f.label,
                "market": f.market,
                "playbook": f.playbook,
                "balance": str(f.balance),
                "twr_pct": str(f.twr_pct),
                "drawdown_pct": str(f.drawdown_pct),
                "max_drawdown_pct": str(f.max_drawdown_pct),
                "money_gain": str(f.money_gain),
                "symbols": list(f.symbols),
                "run_keys": list(f.run_keys),
            }
            for f in funds
        ],
        "runs": [
            {
                "key": r.key,
                "symbol": r.symbol,
                "playbook": r.playbook,
                "fund": r.fund_label,
                "gain_pct": _dec(r.gain_pct),
                "takes": r.takes,
                "stops": r.stops,
                "trades": r.trades,
            }
            for r in rows
        ],
        "curve": [
            {
                "at": p.at.isoformat(),
                "symbol": p.symbol,
                "gain_pct": str(p.gain_pct),
                "cum_pct": str(p.cum_pct),
            }
            for p in curve
        ],
        "equity_monthly": equity_mod.to_rows(equity),
        "configured": configured,
        "default_to": default_to,
    }
