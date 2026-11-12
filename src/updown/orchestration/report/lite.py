"""가벼운 첫 화면 재료 — 계좌 · 자본금 · 손익 · 열린 포지션만 (사용자 2026-11-06).

화면 폴링이 서버 CPU 의 큰 몫이라, 처음 뜨는 화면은 거래소를 거의 묻지 않고 꼭 볼 것만 낸다.
상세는 사람이 고를 때만 콘솔로 간다. 판단은 없다 — 받은 값을 모아 적을 뿐이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from updown.orchestration.report.performance import (
    Performance,
    book_kind,
    book_order_id,
    book_row_time,
    book_symbol,
)


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
    shares_of: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    stops: Sequence[Mapping[str, Any]] | None = None,
    halted: bool | None = None,
    engine_of: Mapping[str, Mapping[str, Any]] | None = None,
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
        shares_of: 종목 → 보유 몫들(`name` · `stop` · `opened_at`) — 어느 다리 · 어느 손절인가.
        stops: 거래소에 걸린 조건부 줄들(`symbol`). 못 읽으면 None.
        halted: 신규 진입 정지 스위치. 모르면 None.
        engine_of: 종목 → 엔진 다리 한 줄 요약 — 있는 종목의 포지션 줄에만 `engine` 칸이 붙는다.

    Returns:
        `{at, total, available, margin_used, notional, account_leverage, unrealized, capital,
        earned, realized_24h, runs, leader, halted, positions}`. 재료가 빠진 칸은 None 이다.

    Note:
        🔴 `earned` 는 입출금을 뺀 값이다 — 입금은 손익이 아니다. 입출금을 못 읽었으면 지갑 전체가
        번 돈으로 보이므로 None 으로 둔다.
    """
    rows: list[dict[str, Any]] = []
    locked: Decimal | None = None
    unrealized: Decimal | None = None
    notional: Decimal | None = None
    stop_counts: dict[str, int] = {}
    for row in stops or ():
        key = str(row.get("symbol", ""))
        stop_counts[key] = stop_counts.get(key, 0) + 1
    if positions is not None:
        locked, unrealized, notional = Decimal(0), Decimal(0), Decimal(0)
        for item in positions:
            size = _dec(item.get("size")) or Decimal(0)
            if size == 0:
                continue
            symbol = str(item.get("symbol", ""))
            margin = _dec(item.get("margin")) or Decimal(0)
            pnl = _dec(item.get("unrealised_pnl")) or Decimal(0)
            value = _dec(item.get("value"))
            locked += margin
            unrealized += pnl
            notional += abs(value) if value is not None else Decimal(0)
            rows.append(
                {
                    "symbol": symbol,
                    "run": (run_of or {}).get(symbol, ""),
                    "side": "long" if size > 0 else "short",
                    "contracts": str(abs(size)),
                    "margin": str(margin),
                    "notional": None if value is None else str(abs(value)),
                    "entry": _text(_dec(item.get("entry_price"))),
                    "mark": _text(_dec(item.get("mark_price"))),
                    "unrealized": str(pnl),
                    "pct": None if margin <= 0 else str(pnl / margin * Decimal(100)),
                    "shares": [dict(share) for share in (shares_of or {}).get(symbol, ())],
                    "exchange_stops": None if stops is None else stop_counts.get(symbol, 0),
                }
            )
            note = (engine_of or {}).get(symbol)
            if note is not None:
                rows[-1]["engine"] = dict(note)
        rows.sort(key=lambda r: Decimal(r["unrealized"]))
    total = None if available is None or locked is None else available + locked
    capital: Decimal | None = None
    if capital_moves is not None:
        capital = sum((_dec(row.get("change")) or Decimal(0) for row in capital_moves), Decimal(0))
    earned = None if total is None or capital is None else total - capital
    return {
        "at": at.isoformat(),
        "total": _text(total),
        "available": _text(available),
        "margin_used": _text(locked),
        "notional": _text(notional),
        "account_leverage": (
            None if total is None or notional is None or total <= 0 else str(notional / total)
        ),
        "unrealized": _text(unrealized),
        "capital": _text(capital),
        "earned": _text(earned),
        "realized_24h": _text(realized_24h),
        "runs": runs,
        "leader": leader,
        "halted": halted,
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


def recent_payload(
    book: Sequence[Mapping[str, str]] | None,
    *,
    limit: int = 50,
    since: datetime | None = None,
    owners: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """최근 실현 매매 목록 — 거래소 자금 원장을 **실현 주문 단위**로 묶는다 (T829 · 순수 함수).

    한 줄 = 손익을 실현한 주문 하나(청산 · 감축). 분할 체결은 한 줄로 합친다. 그 종목의 앞
    실현 뒤부터 쌓인 수수료(진입 + 청산)와 펀딩을 그 줄에 붙여 순손익을 낸다.

    Args:
        book: `account_book` 줄들(`type` · `change` · `time` · `text` · `contract` · `balance`).
            못 읽었으면 None.
        limit: 보여 줄 건수(최신부터).
        since: 손익 시작점(aware). 주면 그 뒤 줄만의 합계를 `after` 에 따로 낸다 — 목록은
            자르지 않는다(옛 매매도 본 일이므로 보여 주고, 합계만 섞이지 않게 가른다).
        owners: `{주문 번호: {leg, side}}` — 그 실현을 낸 주문의 원장 매매(다리 이름 · 롱/숏).
            주문 이름(`t-…` 매매 id · 발동 손절 `ao-…` 는 원장의 손절 주문 번호)으로 이은 것만
            든다 — 시각으로 맞추지 않는다(사용자 2026-11-12). 못 이은 줄은 `leg` · `side` 가 None.

    Returns:
        `{rows, count, wins, net, pnl, fees, funding, since, after, open_fees, open_funding,
        oldest, unreadable}`. `rows` 의 한 줄은 `{symbol, at, pnl, fees, funding, net, balance,
        fills, leg, side}`, `after` 는 `{count, wins, net, pnl, fees, funding}`
        (시작점이 없으면 None).
        장부를 못 읽었으면 `rows` 는 None 이다(빈 목록으로 꾸미지 않는다 · 규칙 #8).

    Note:
        - `balance` 는 그 주문의 마지막 줄이 적힌 **직후의 지갑 잔고**(거래소가 적은 값)다.
          미실현은 들어 있지 않다 — 그 시각의 미실현 포함 평가는 어디에도 기록이 없다.
        - 방향 · 다리 이름은 자금 원장에 없어 `owners`(주문 번호로 이은 원장 매매)에서 붙인다.
        - 거래소가 주는 장부는 약 30 일치다. `oldest` 보다 앞서 들어간 매매는 진입 수수료 ·
          앞 펀딩이 빠진 채로 나온다.
        - `open_*` 는 마지막 실현 뒤에 쌓여 아직 어느 줄에도 안 붙은 몫(열린 포지션의 진입
          수수료 · 펀딩)이다.
    """
    if book is None:
        return {"rows": None}
    ordered: list[tuple[datetime, int, int, Mapping[str, str], Decimal]] = []
    unreadable = 0
    for index, row in enumerate(book):
        when = book_row_time(row)
        change = _dec(row.get("change"))
        if when is None or change is None:
            unreadable += 1
            continue
        try:
            serial = int(str(row.get("id", "")))
        except ValueError:
            serial = 0
        # 같은 초 · 번호 없는 줄은 받은 순서(최신 먼저)를 뒤집어 옛 → 새 로 둔다.
        ordered.append((when, serial, -index, row, change))
    ordered.sort(key=lambda item: item[:3])
    zero = Decimal(0)
    fees: dict[str, Decimal] = {}
    funding: dict[str, Decimal] = {}
    closes: dict[str, dict[str, Any]] = {}
    for when, _, _, row, change in ordered:
        kind = book_kind(row)
        symbol = book_symbol(row)
        key = str(row.get("text", "")) or f"{symbol}@{when.isoformat()}"
        open_close = closes.get(key)
        if kind == "fund":
            funding[symbol] = funding.get(symbol, zero) + change
            continue
        if kind == "fee":
            if open_close is None:
                fees[symbol] = fees.get(symbol, zero) + change
                continue
            open_close["fees"] += change
        elif kind == "pnl":
            if open_close is None:
                open_close = closes[key] = {
                    "symbol": symbol,
                    "order": book_order_id(row),
                    "pnl": zero,
                    "fees": zero,
                    "funding": zero,
                    "fills": 0,
                }
            open_close["pnl"] += change
            open_close["fees"] += fees.pop(symbol, zero)
            open_close["funding"] += funding.pop(symbol, zero)
            open_close["fills"] += 1
        else:
            continue
        open_close["at"] = when
        open_close["balance"] = _dec(row.get("balance"))
    shown = sorted(closes.values(), key=lambda item: item["at"], reverse=True)[: max(limit, 0)]
    owned = owners or {}
    rows = [
        {
            "leg": owned.get(item["order"], {}).get("leg"),
            "side": owned.get(item["order"], {}).get("side"),
            "symbol": item["symbol"],
            "at": item["at"].isoformat(),
            "pnl": str(item["pnl"]),
            "fees": str(item["fees"]),
            "funding": str(item["funding"]),
            "net": str(item["pnl"] + item["fees"] + item["funding"]),
            "balance": _text(item["balance"]),
            "fills": item["fills"],
        }
        for item in shown
    ]

    def totals(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        pnl_sum = sum((item["pnl"] for item in items), zero)
        fee_sum = sum((item["fees"] for item in items), zero)
        fund_sum = sum((item["funding"] for item in items), zero)
        return {
            "count": len(items),
            "wins": sum(1 for item in items if item["pnl"] + item["fees"] + item["funding"] > 0),
            "net": str(pnl_sum + fee_sum + fund_sum),
            "pnl": str(pnl_sum),
            "fees": str(fee_sum),
            "funding": str(fund_sum),
        }

    return {
        "rows": rows,
        **totals(shown),
        "since": None if since is None else since.isoformat(),
        "after": None if since is None else totals([i for i in shown if i["at"] >= since]),
        "open_fees": str(sum(fees.values(), zero)),
        "open_funding": str(sum(funding.values(), zero)),
        "oldest": ordered[0][0].isoformat() if ordered else None,
        "unreadable": unreadable,
    }
