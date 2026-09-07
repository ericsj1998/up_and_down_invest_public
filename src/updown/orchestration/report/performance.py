"""성과 집계 — 원장과 거래소를 **나란히** (T35).

🔴 **근거 둘을 따로 낸다.** 원장(`wf_trades`)은 우리 주장이고 거래소 자금 원장
(`account_book`)은 사실이다. 원장이 `half_price` 를 흘려 37건이 +64.77% 로 찍힌 적이
있다 — 그 숫자가 메일로 나갔으면 되돌릴 수 없었다. 둘이 갈리면 **갈렸다고 쓴다.**

⚠️ **비용을 따로 낸다.** 손실의 80.6% 가 회전 수수료였던 적이 있다 — 합계만 보면
전략 탓으로 읽는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa

from updown.common.db.models.walkforward import WalkforwardRun, WalkforwardTrade
from updown.orchestration.walkforward.ledger import Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.store import _to_record  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class Window:
    """집계 구간 (UTC · 끝 미포함)."""

    since: datetime
    until: datetime

    def __post_init__(self) -> None:
        """naive·빈 구간을 거부한다 — 조용히 틀린 구간으로 집계하지 않는다 (규칙 #8)."""
        if self.since.tzinfo is None or self.until.tzinfo is None:
            raise ValueError("집계 구간은 UTC aware 여야 한다 (규칙 #7)")
        if self.since >= self.until:
            raise ValueError(f"구간이 비었다 — {self.since} >= {self.until}")


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    """원장 쪽 집계 — 우리 주장.

    Attributes:
        gain_sum_pct: 건별 손익률 합 (증거금 기준 · 배율 반영). 복리가 아니라 합이다.
        max_drawdown_pct: 손익률 누적 경로의 최대 낙폭 (합 기준).
    """

    trades: int = 0
    longs: int = 0
    shorts: int = 0
    wins: int = 0
    gain_sum_pct: Decimal = Decimal(0)
    mean_rr: Decimal | None = None
    max_drawdown_pct: Decimal = Decimal(0)
    by_outcome: dict[str, int] = field(default_factory=dict[str, int])
    by_playbook: dict[str, int] = field(default_factory=dict[str, int])
    by_symbol: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def win_rate(self) -> Decimal | None:
        """승률(%). 청산이 없으면 None."""
        if self.trades == 0:
            return None
        return Decimal(self.wins) / Decimal(self.trades) * Decimal(100)


@dataclass(frozen=True, slots=True)
class ExchangeSummary:
    """거래소 자금 원장 집계 — 사실.

    Note:
        Gate `account_book` 의 `type`: `pnl`(실현 손익) · `fee`(수수료) · `fund`(펀딩).
        그 외(`dnw` 입출금 · `refr` 리베이트 등)는 성과가 아니라 센다만 한다.
    """

    pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    rows: int = 0
    other: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def net(self) -> Decimal:
        """순 실현 = 손익 + 수수료 + 펀딩 (수수료·펀딩은 보통 음수)."""
        return self.pnl + self.fees + self.funding


@dataclass(frozen=True, slots=True)
class Performance:
    """리포트 한 장의 재료."""

    window: Window
    ledger: LedgerSummary
    exchange: ExchangeSummary | None
    diverged: bool
    note: str


def summarize_records(records: Sequence[tuple[str, TradeRecord]], window: Window) -> LedgerSummary:
    """구간 안에 **청산된** 매매만 집계한다 (순수 함수).

    Args:
        records: `(종목, 기록)` 들. 청산 시각이 구간 밖이면 뺀다.
        window: 집계 구간.

    Returns:
        원장 집계.

    Note:
        ⚠️ 청산 시각(`closed_at`) 기준이다. 진입 기준으로 잡으면 아직 안 끝난 매매의
        손익을 센다. 강제청산·취소는 결과별 표에는 남기되, 취소는 매매 수에서 뺀다.
    """
    closed = [
        (symbol, item)
        for symbol, item in records
        if item.closed_at is not None
        and window.since <= item.closed_at < window.until
        and item.outcome is not Outcome.CANCELLED
    ]
    if not closed:
        return LedgerSummary()

    gains = [(item.gain_pct or Decimal(0)) for _, item in closed]
    running = Decimal(0)
    peak = Decimal(0)
    drawdown = Decimal(0)
    for gain in gains:
        running += gain
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    rrs = [item.realized_rr for _, item in closed if item.realized_rr is not None]

    return LedgerSummary(
        trades=len(closed),
        longs=sum(1 for _, item in closed if item.direction is Direction.LONG),
        shorts=sum(1 for _, item in closed if item.direction is Direction.SHORT),
        wins=sum(1 for gain in gains if gain > 0),
        gain_sum_pct=sum(gains, Decimal(0)),
        mean_rr=(sum(rrs, Decimal(0)) / len(rrs)) if rrs else None,
        max_drawdown_pct=drawdown,
        by_outcome=dict(Counter(item.outcome.value for _, item in closed)),
        by_playbook=dict(Counter(item.playbook for _, item in closed)),
        by_symbol=dict(Counter(symbol for symbol, _ in closed)),
    )


_KIND_ALIASES = {
    # 바이낸스 incomeType → Gate account_book 어휘 (BN account_book 은 원문 그대로 준다)
    "REALIZED_PNL": "pnl",
    "COMMISSION": "fee",
    "FUNDING_FEE": "fund",
}


def summarize_account_book(rows: Sequence[Mapping[str, str]], window: Window) -> ExchangeSummary:
    """거래소 자금 원장을 구간으로 잘라 합친다 (순수 함수).

    Args:
        rows: `account_book` 행들 — `{type, change, time, ...}`. `time` 은 초 단위.
        window: 집계 구간.

    Returns:
        거래소 집계. 읽을 수 없는 행은 건너뛰되 `other` 에 센다 — 조용히 0 이 되지 않게.
    """
    pnl = fees = funding = Decimal(0)
    counted = 0
    other: Counter[str] = Counter()
    for row in rows:
        try:
            stamp = float(row.get("time", ""))
            # ⭐ 바이낸스 income 은 ms, Gate account_book 은 초다 — 단위를 안 맞추면
            #   바이낸스 행 전부가 구간 밖(서기 5만 년)으로 빠져 조용히 0 이 된다.
            when = datetime.fromtimestamp(stamp / 1000 if stamp > 1e12 else stamp, UTC)
            change = Decimal(str(row.get("change", "0")))
        except (ValueError, ArithmeticError):
            other["unreadable"] += 1
            continue
        if not (window.since <= when < window.until):
            continue
        counted += 1
        kind = _KIND_ALIASES.get(str(row.get("type", "")), str(row.get("type", "")))
        if kind == "pnl":
            pnl += change
        elif kind == "fee":
            fees += change
        elif kind == "fund":
            funding += change
        else:
            other[kind or "?"] += 1
    return ExchangeSummary(pnl=pnl, fees=fees, funding=funding, rows=counted, other=dict(other))


async def load_closed_trades(
    factory: async_sessionmaker[AsyncSession],
    window: Window,
    *,
    market: str | None = None,
) -> list[tuple[str, TradeRecord]]:
    """구간 안에 청산된 **라이브 판**의 매매를 전부 읽는다 — 판 경계를 넘어 합친다.

    Args:
        factory: DB 세션 팩토리.
        window: 집계 구간.
        market: 거래소 코드. 주면 그 거래소 판만 (거래소별 리포트 · 2026-08-26).

    Returns:
        `(종목, 기록)`. 백테스트 판은 섞지 않는다 — 라이브와 백테스트는 목록을 공유하지
        않는다 (사용자 확정).
    """
    query = (
        sa.select(WalkforwardTrade, WalkforwardRun.symbol)
        .join(WalkforwardRun, WalkforwardRun.id == WalkforwardTrade.run_id)
        .where(WalkforwardRun.live.is_(True))
        .where(WalkforwardTrade.closed_at.is_not(None))
        .where(WalkforwardTrade.closed_at >= window.since)
        .where(WalkforwardTrade.closed_at < window.until)
        .order_by(WalkforwardTrade.closed_at)
    )
    if market is not None:
        query = query.where(WalkforwardRun.market == market)
    async with factory() as session:
        rows = (await session.execute(query)).all()
    return [(str(symbol), _to_record(row)) for row, symbol in rows]


def compare(ledger: LedgerSummary, exchange: ExchangeSummary | None) -> tuple[bool, str]:
    """원장과 거래소가 **갈리는가** — 방향(부호)으로 본다.

    Args:
        ledger: 원장 집계.
        exchange: 거래소 집계. 못 읽었으면 None.

    Returns:
        `(갈렸는가, 사람이 읽을 이유)`.

    Note:
        원장은 증거금 대비 %, 거래소는 USDT 라 크기를 직접 비교할 수 없다. 그래서
        **부호가 다르면** 갈린 것으로 쓴다 — 한쪽이 벌었다는데 다른 쪽이 잃었으면
        둘 중 하나는 틀렸고, 어느 쪽인지는 사람이 본다.
    """
    if exchange is None:
        return False, "거래소 원장을 못 읽었다 — 원장 숫자만 있다. 대조 없이 읽지 않는다"
    if exchange.rows == 0 and ledger.trades == 0:
        return False, "구간에 매매가 없다"
    if exchange.rows == 0 or ledger.trades == 0:
        return True, "한쪽에만 기록이 있다 — 원장과 거래소가 갈렸다"
    ledger_sign = 1 if ledger.gain_sum_pct > 0 else (-1 if ledger.gain_sum_pct < 0 else 0)
    exchange_sign = 1 if exchange.net > 0 else (-1 if exchange.net < 0 else 0)
    if ledger_sign and exchange_sign and ledger_sign != exchange_sign:
        return True, "손익 부호가 갈렸다 — 원장과 거래소 중 하나는 틀렸다"
    return False, "원장과 거래소의 손익 부호가 같다"


def _counts(table: Mapping[str, int]) -> str:
    """`{이름: 건수}` 를 한 줄로 — 비면 `—`."""
    return " · ".join(f"{k} {v}" for k, v in sorted(table.items())) or "—"


def render_text(perf: Performance) -> str:
    """평문 메일 본문 — **집계만** 싣는다 (잔고 상세·키는 싣지 않는다).

    Args:
        perf: 원장·거래소 집계.

    Returns:
        평문 본문.
    """
    led = perf.ledger
    lines = [
        f"구간  {perf.window.since:%Y-%m-%d %H:%M} ~ {perf.window.until:%Y-%m-%d %H:%M} UTC",
        "",
        "[원장 — 우리 주장]",
    ]
    if led.trades == 0:
        # 🔴 매매가 없으면 빈 항목("승률 — · 결과별 — · 종목별 —")을 늘어놓지 않는다
        #    (사용자 2026-09-04: *"아무 주문도 없었으면 리포트에서도 안 나오게"*). 없는 것을
        #    한 줄로 말하는 편이 여덟 줄의 대시보다 정확하다.
        lines.append("  이 구간 매매 없음")
    else:
        lines += [
            f"  매매 {led.trades}건 · 롱 {led.longs} · 숏 {led.shorts}",
            f"  승률 {led.win_rate:.1f}%" if led.win_rate is not None else "  승률 —",
            f"  손익률 합 {led.gain_sum_pct:+.2f}% · 최대 낙폭 {led.max_drawdown_pct:.2f}%",
            f"  평균 RR {led.mean_rr:.2f}" if led.mean_rr is not None else "  평균 RR —",
            f"  결과별 {_counts(led.by_outcome)}",
            f"  종목별 {_counts(led.by_symbol)}",
            f"  매매법 {_counts(led.by_playbook)}",
        ]
    lines += ["", "[거래소 — 사실]"]
    if perf.exchange is None:
        lines.append("  읽지 못했다")
    else:
        exc = perf.exchange
        lines += [
            f"  실현 손익 {exc.pnl:+.4f} USDT · 수수료 {exc.fees:+.4f} · 펀딩 {exc.funding:+.4f}",
            f"  순 실현 {exc.net:+.4f} USDT · 원장 행 {exc.rows}",
        ]
    lines += ["", f"[대조] {'🔴 갈렸다' if perf.diverged else '✅ 일치'} — {perf.note}"]
    return "\n".join(lines)


def render_market_breakdown(slices: Mapping[str, Performance]) -> str:
    """전체 리포트 뒤에 붙는 **거래소별** 절 (사용자 요구 2026-08-26).

    전체 취합만 있으면 어느 거래소가 벌고 어느 거래소가 잃는지 안 보인다 —
    같은 전략이라도 수수료·펀딩·체결 품질이 거래소마다 다르다.

    Args:
        slices: 거래소 이름 → 그 거래소만의 집계.

    Returns:
        `[거래소별]` 절 평문.
    """
    lines = ["", "[거래소별]"]
    for name, perf in sorted(slices.items()):
        led = perf.ledger
        head = f"  {name}: 매매 {led.trades}건 · 손익률 합 {led.gain_sum_pct:+.2f}%"
        if perf.exchange is None:
            lines.append(head + " · 거래소 원장 못 읽음")
        else:
            exc = perf.exchange
            lines.append(
                head
                + f" · 거래소 순 실현 {exc.net:+.4f} USDT"
                + f" (손익 {exc.pnl:+.4f} · 수수료 {exc.fees:+.4f} · 펀딩 {exc.funding:+.4f})"
            )
    return "\n".join(lines)


def render_funds(rows: Sequence[Mapping[str, str]]) -> str:
    """리밸런싱 펀드 절 — 파일 스냅샷 기준 (사용자 요구 2026-08-26).

    Args:
        rows: 펀드 스냅샷 행들.

    Returns:
        `[리밸런싱 펀드]` 절 평문. 행이 없으면 빈 문자열.

    Note:
        값은 `logs/funds/*.json` 의 마지막 저장 시점이다 — 러너가 없는 프로세스
        (스케줄러)에서도 읽을 수 있는 유일한 원천이라 이것을 쓴다. 없으면 절 자체를
        내지 않는다 (빈 표를 꾸미지 않는다 · 규칙 #8).
    """
    if not rows:
        return ""
    lines = ["", "[리밸런싱 펀드 — 파일 스냅샷]"]
    for row in rows:
        twr = Decimal(str(row.get("twr", "1") or "1"))
        lines.append(
            f"  {row.get('label', '?')} ({row.get('market', '?')} · {row.get('playbook', '?')}):"
            f" 평가 {row.get('equity', '?')} USDT · TWR {(twr - 1) * 100:+.1f}%"
        )
    return "\n".join(lines)
