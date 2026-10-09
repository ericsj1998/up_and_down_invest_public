"""손익 귀속 — 매매 한 건의 **돈**(거래소 기록 우선)과 다리별 손실 몫 (T444 · 순수)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from updown.orchestration.live_review.snapshot import Trade, dec, when

MATCH_WINDOW = timedelta(hours=36)
"""닫힌 포지션 기록과 원장 매매를 잇는 시각 창 — id 앞자리(8자)만 맞추므로 시각으로 한 번 더."""


def trade_prefix_of_text(text: str) -> str:
    """거래소 text 의 매매 id 앞자리 — `t-<판6>-<매매8>-…` · 옛 `t-<매매12>-…`. 아니면 빈 문자열."""
    if not text.startswith("t-"):
        return ""
    parts = text.split("-")
    if len(parts) >= 3 and len(parts[1]) == 6 and len(parts[2]) >= 6:
        return parts[2]
    return parts[1] if len(parts) >= 2 else ""


@dataclass(frozen=True, slots=True)
class TradeMoney:
    """매매 한 건의 결과 — 돈(USDT) · R · 출처."""

    trade: Trade
    pnl: Decimal | None
    """실현 손익(수수료 · 펀딩 포함 · USDT). 거래소 기록 우선 · 없으면 원장 추정 · 없으면 None."""
    source: str
    """`exchange`(position_close) · `ledger`(진입 · 청산가 · 증거금 · 배율로 추정) · `none`."""
    r: Decimal | None
    """실현 R = (청산가 - 진입가) ÷ (진입가 - 계획 손절) · 방향 맞춤. 손절 계획이 없으면 None."""


def _ledger_pnl(trade: Trade) -> Decimal | None:
    if (
        trade.entry is None
        or trade.exit_price is None
        or trade.margin_used is None
        or trade.leverage is None
        or trade.entry == 0
    ):
        return None
    move = (trade.exit_price - trade.entry) / trade.entry
    gross = (move if trade.is_long else -move) * trade.margin_used * trade.leverage
    fees = trade.fee_actual or Decimal(0)
    funding = trade.funding_paid or Decimal(0)
    return gross - abs(fees) - funding


def money_of(trade: Trade, closes: list[dict[str, Any]]) -> TradeMoney:
    """매매 한 건의 돈 — 거래소 `position_close`(text 앞자리 + 시각 창) 우선, 없으면 원장 추정.

    Args:
        trade: 원장 매매.
        closes: 거래소 닫힌 포지션 기록(`time` · `contract` · `pnl` · `text`).

    Returns:
        `TradeMoney`. 열린 매매는 pnl None · source `open`.
    """
    r = trade.r_of(trade.exit_price) if trade.exit_price is not None else None
    if not trade.closed:
        return TradeMoney(trade, None, "open", None)
    hits: list[Decimal] = []
    for row in closes:
        if str(row.get("contract", "")) != trade.symbol:
            continue
        prefix = trade_prefix_of_text(str(row.get("text", "")))
        if not prefix or not trade.trade_id.startswith(prefix):
            continue
        at = when(_epoch_iso(row.get("time")))
        if at is None or trade.closed_at is None or abs(at - trade.closed_at) > MATCH_WINDOW:
            continue
        pnl = dec(row.get("pnl"))
        if pnl is not None:
            hits.append(pnl)
    if hits:
        return TradeMoney(trade, sum(hits, Decimal(0)), "exchange", r)
    est = _ledger_pnl(trade)
    return TradeMoney(trade, est, "ledger" if est is not None else "none", r)


def _epoch_iso(v: object) -> str:
    try:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(float(str(v)), UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


@dataclass(frozen=True, slots=True)
class LegRow:
    """다리 한 줄 — 건수 · 승 · 패 · 손익 합 · 손실 합 · 손실 몫 · 평균 R · 승률."""

    leg: str
    n: int
    wins: int
    losses: int
    pnl: Decimal
    loss_sum: Decimal
    loss_share_pct: Decimal | None
    mean_r: Decimal | None
    win_rate_pct: Decimal | None
    open_n: int


def leg_table(rows: list[TradeMoney]) -> list[LegRow]:
    """다리별 표 — 손실 몫 = 그 다리 손실 합 ÷ 전체 손실 합(%). 열린 매매는 따로 센다.

    Args:
        rows: `money_of` 결과들.

    Returns:
        손익 합 오름차순(가장 많이 잃은 다리가 위).
    """
    by: dict[str, list[TradeMoney]] = defaultdict(list)
    for row in rows:
        by[row.trade.leg].append(row)
    total_loss = sum((abs(x.pnl) for x in rows if x.pnl is not None and x.pnl < 0), Decimal(0))
    out: list[LegRow] = []
    for leg, items in by.items():
        closed = [x for x in items if x.pnl is not None]
        wins = sum(1 for x in closed if x.pnl is not None and x.pnl > 0)
        losses = sum(1 for x in closed if x.pnl is not None and x.pnl < 0)
        pnl = sum((x.pnl for x in closed if x.pnl is not None), Decimal(0))
        loss_sum = sum((abs(x.pnl) for x in closed if x.pnl is not None and x.pnl < 0), Decimal(0))
        rs = [x.r for x in closed if x.r is not None]
        out.append(
            LegRow(
                leg=leg,
                n=len(closed),
                wins=wins,
                losses=losses,
                pnl=pnl,
                loss_sum=loss_sum,
                loss_share_pct=(loss_sum / total_loss * 100) if total_loss > 0 else None,
                mean_r=(sum(rs, Decimal(0)) / len(rs)) if rs else None,
                win_rate_pct=(Decimal(wins) / len(closed) * 100) if closed else None,
                open_n=sum(1 for x in items if x.source == "open"),
            )
        )
    return sorted(out, key=lambda row: row.pnl)


def outside_money(account_book: list[dict[str, Any]], order_texts: dict[str, str]) -> Decimal:
    """같은 계좌의 **매매법 밖 주문**(사람) 손익 · 수수료 합 — 장부 `계약:주문번호` ↔ 끝난 주문.

    Args:
        account_book: 거래소 자금 원장 줄들.
        order_texts: `{주문번호: text}`.

    Returns:
        매매법 밖 주문의 pnl + fee 합(보통 음수). 번호를 못 찾은 줄은 안 센다.
    """
    total = Decimal(0)
    for row in account_book:
        if str(row.get("type", "")) not in ("pnl", "fee"):
            continue
        text = str(row.get("text", ""))
        _, sep, oid = text.partition(":")
        if not sep:
            continue
        kind = order_texts.get(oid.strip())
        if kind is None or kind.startswith("t-"):
            continue
        chg = dec(row.get("change"))
        if chg is not None:
            total += chg
    return total


def mark_stale_open(rows: list[TradeMoney], closed_run_ids: frozenset[str]) -> list[TradeMoney]:
    """열린 원장 줄 중 판이 이미 닫힌 것은 `stale` 로 표시 — 거래소엔 그 포지션이 없다 (T409).

    Args:
        rows: `money_of` 결과들.
        closed_run_ids: 닫힌 판의 id 들.

    Returns:
        같은 순서의 목록. `open` 이면서 판이 닫힌 줄만 `source="stale"` 로 바뀐다.
    """
    from dataclasses import replace

    return [
        replace(r, source="stale") if r.source == "open" and r.trade.run_id in closed_run_ids else r
        for r in rows
    ]


def fill_basis(
    trade: Trade, finished_orders: list[dict[str, Any]]
) -> tuple[Decimal, datetime] | None:
    """진입 **체결가 · 체결 시각** — 거래소 끝난 주문 `t-<판6>-<매매8>-en-…`(T446 Q3 · 2026-10-09).

    Args:
        trade: 원장 매매.
        finished_orders: 끝난 주문 목록(`contract` · `text` · `fill_price` · `create_time`).

    Returns:
        (체결가, 체결 시각). 없으면 None — 그러면 호출부는 원장 진입가 · opened_at 을 쓴다.

    Note:
        원장 `entry` · `opened_at` 은 낡은 5분봉 기준이라(T372) 체결보다 중앙 10분 이르고 10bp 안팎
        다르다. 경로 셈은 체결 뒤 봉만 봐야 한다 — 체결 전 봉 저가를 물림으로 세면 한 건이 뒤집혔다.
    """
    prefix = trade.trade_id[:8]
    best: tuple[datetime, Decimal] | None = None
    for o in finished_orders:
        if str(o.get("contract", "")) != trade.symbol:
            continue
        text = str(o.get("text", ""))
        if not text.startswith("t-") or f"-{prefix}-en" not in text:
            continue
        if str(o.get("finish_as", "filled")) not in ("filled", ""):
            continue
        px = dec(o.get("fill_price"))
        at = when(_epoch_iso(o.get("finish_time") or o.get("create_time")))
        if px is None or px <= 0 or at is None:
            continue
        if best is None or at < best[0]:
            best = (at, px)
    return None if best is None else (best[1], best[0])
