"""이메일 조립 — 라이브 판을 읽어 판별 섹션(차트·주문표)으로 만든다 (T55).

`runcharts`(자료 변환) + `chart`(그리기) + `html`(렌더)을 **호출만** 한다 — 조립층의
본분이다 (CLAUD.md orchestration 입주 조건). 계좌 스냅샷은 위(apps)에서 주입받는다:
금고·잔액은 살아 있는 러너/거래소에 있고, 그것을 아래로 내려보내면 경계가 뒤집힌다.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa

from updown.analysis.playbook.select import load_playbooks
from updown.common.db.models.walkforward import WalkforwardRun, WalkforwardTrade
from updown.common.domain.instrument import Market, Timeframe
from updown.orchestration.report import equity
from updown.orchestration.report.chart import render_equity_chart, render_run_chart
from updown.orchestration.report.dashboard import RunRow
from updown.orchestration.report.funds import load_fund_snapshots
from updown.orchestration.report.html import (
    FundSection,
    RunSection,
    TradeRow,
    render_console_email,
    render_run_email,
)
from updown.orchestration.report.runcharts import load_candles, meta_for, to_chart_trade
from updown.orchestration.walkforward.store import _to_record  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from updown.orchestration.report.html import AccountSummary
    from updown.orchestration.report.performance import Window
    from updown.orchestration.walkforward.ledger import TradeRecord


def _timeframe_of(attribution: str) -> Timeframe:
    """판 귀속 키 → 봉 축. 못 찾으면 주력 15m (라이브 세트 축)."""
    for book in load_playbooks():
        if book.attribution == attribution or book.playbook_id == attribution:
            return book.timeframe
    return Timeframe.M15


def _when(ts: datetime) -> str:
    """시각을 사람이 읽는 짧은 문자열로 (UTC, 초 버림)."""
    return ts.strftime("%m-%d %H:%M")


def _trade_rows(records: list[TradeRecord]) -> list[TradeRow]:
    """원장 기록들을 주문표 줄로 — 진입 시각 오름차순."""
    rows: list[TradeRow] = []
    for r in sorted(records, key=lambda x: x.opened_at or x.placed_at):
        if r.opened_at is None:
            continue
        rows.append(
            TradeRow(
                direction=str(r.direction),
                entry=r.entry,
                exit=r.exit_average,
                gain_pct=r.gain_pct,
                outcome=str(r.outcome),
                leverage=r.leverage,
                when=_when(r.opened_at),
            )
        )
    return rows


async def _live_runs_with_trades(
    factory: async_sessionmaker[AsyncSession], window: Window
) -> list[tuple[WalkforwardRun, list[TradeRecord]]]:
    """구간에 **활성이었던** 매매를 라이브 판별로 묶어 읽는다.

    Note:
        차트는 청산된 것뿐 아니라 **아직 보유 중인** 매매도 그린다 (보유중 회색 박스).
        그래서 `closed_at >= since` 또는 아직 안 닫힘(열려서 구간에 걸침)까지 담는다.
        라이브만 — 백테스트 판은 섞지 않는다 (사용자 확정).

        🔴 **안 닫힌 매매는 판이 살아 있을 때만** 센다 (2026-09-05 사용자 신고: 대시보드에 판 66개).
        죽은 판이 매매를 닫지 못한 채 남으면 그 매매는 영원히 "보유 중"이라 매일 구간에 잡혔다 —
        dev DB 판 298개 중 살아 있는 11개 말고도 수십 개가 그렇게 떴다. 닫힌 판의 매매는 청산이
        구간 안에 있을 때만 싣는다.
    """
    async with factory() as session:
        rows = (
            await session.execute(
                sa.select(WalkforwardTrade, WalkforwardRun)
                .join(WalkforwardRun, WalkforwardRun.id == WalkforwardTrade.run_id)
                .where(WalkforwardRun.live.is_(True))
                .where(WalkforwardTrade.opened_at.is_not(None))
                .where(WalkforwardTrade.opened_at < window.until)
                .where(
                    sa.or_(
                        sa.and_(
                            WalkforwardTrade.closed_at.is_(None),
                            WalkforwardRun.closed_at.is_(None),
                        ),
                        WalkforwardTrade.closed_at >= window.since,
                    )
                )
                .order_by(WalkforwardRun.opened_at, WalkforwardTrade.opened_at)
            )
        ).all()
    grouped: dict[str, tuple[WalkforwardRun, list[TradeRecord]]] = {}
    for trade, run in rows:
        bucket = grouped.setdefault(str(run.id), (run, []))
        bucket[1].append(_to_record(trade))
    return list(grouped.values())


def fund_labels_by_run(root: Path | None = None) -> dict[str, str]:
    """판 키 → 펀드 이름 (파일 스냅샷 `logs/funds/*.json` 의 `runs`).

    Args:
        root: 펀드 스냅샷 디렉터리. None 이면 `FUNDS_ROOT` 또는 `logs/funds`.

    Returns:
        `{판 키: 펀드 이름}`. 파일이 없거나 깨졌으면 빈 매핑.

    Note:
        메모리(`rebalancer.FUNDS`)가 아니라 **파일**을 읽는다 — 리포트는 러너가 없는
        프로세스에서도 조립된다. `runs` 는 2026-09-04 부터 저장되므로 그 전 파일은 빈 매핑이다
        (그러면 판이 "개별 판" 으로 뜬다 — 틀린 이름을 지어내는 것보다 낫다).
    """
    base = root or Path(os.environ.get("FUNDS_ROOT", "logs/funds"))
    found: dict[str, str] = {}
    for path in sorted(base.glob("*.json")) if base.exists() else []:
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        data = cast("dict[str, Any]", raw)
        label = str(data.get("label", path.stem))
        runs = data.get("runs")
        if isinstance(runs, dict):
            for handle in cast("dict[str, Any]", runs).values():
                found[str(handle)] = label
    return found


async def build_sections(
    factory: async_sessionmaker[AsyncSession],
    window: Window,
    *,
    fund_of: Mapping[str, str] | None = None,
) -> list[RunSection]:
    """라이브 판마다 차트·주문표 섹션을 만든다.

    Args:
        factory: DB 세션 팩토리.
        window: 집계 구간.
        fund_of: 판 키 → 펀드 이름. 주면 섹션에 `fund_label` 이 붙어 펀드별로 묶인다.

    Returns:
        판별 섹션들. 손익률 큰 순으로 정렬(없는 것은 뒤로).
    """
    sections: list[RunSection] = []
    labels = fund_of or {}
    for run, records in await _live_runs_with_trades(factory, window):
        timeframe = _timeframe_of(run.playbook)
        candles = await load_candles(
            factory,
            market=Market(run.market),
            symbol=run.symbol,
            timeframe=timeframe,
            window=window,
        )
        chart_trades = [ct for r in records if (ct := to_chart_trade(r)) is not None]
        meta = meta_for(
            symbol=run.symbol,
            playbook=run.playbook_id,
            window=window,
            timeframe=str(timeframe),
            records=records,
            equity_before=None,  # 24h 전 판 자본은 리플레이가 있어야 안다 → 후속(안 지어낸다)
        )
        png = render_run_chart(candles=candles, trades=chart_trades, meta=meta)
        sections.append(
            RunSection(
                symbol=run.symbol,
                playbook=run.playbook_id,
                period_label=meta.period_label,
                gain_pct=meta.gain_pct,
                equity_before=meta.equity_before,
                takes=meta.takes,
                stops=meta.stops,
                chart_b64=base64.b64encode(png).decode(),
                trades=_trade_rows(records),
                # ⚠️ `run.id` 는 UUID, 펀드가 들고 있는 handle 은 `run.key` 다 (실측 2026-09-04)
                fund_label=labels.get(run.key),
            )
        )
    sections.sort(key=lambda s: (s.gain_pct is None, -(s.gain_pct or Decimal(0))))
    return sections


async def build_console(
    factory: async_sessionmaker[AsyncSession],
    window: Window,
    account: AccountSummary,
    *,
    when: str,
) -> str:
    """콘솔(전체) 이메일 HTML — 계좌 요약 + 펀드별 판 세부 (주문 없으면 한 줄).

    Args:
        factory: DB 세션 팩토리.
        window: 구간.
        account: 계좌 요약.
        when: 제목줄에 찍을 시각 문자열.

    Returns:
        HTML 본문.
    """
    sections = await build_sections(factory, window, fund_of=fund_labels_by_run())
    return render_console_email(
        account,
        sections,
        when=when,
        funds=fund_sections(sections),
        equity_b64=equity_chart_b64(),
    )


def fund_sections(sections: Sequence[RunSection]) -> list[FundSection]:
    """펀드 파일 스냅샷마다 카드 하나 — 그 펀드가 소유한 판 섹션을 안에 넣는다 (사용자 2026-09-07).

    Args:
        sections: `build_sections` 결과 (`fund_label` 이 붙어 있다).

    Returns:
        펀드 카드들. 펀드 파일이 없으면 빈 목록 — 빈 표를 꾸미지 않는다.

    Note:
        붙이는 열쇠는 펀드 **이름**이다 — `build_sections` 가 `fund_labels_by_run()` 으로 판 키 →
        이름을 이미 풀어 놓았다. 같은 이름의 펀드가 둘이면 둘 다 같은 판을 보인다 (이름을 다르게
        두는 것이 맞다).
    """
    out: list[FundSection] = []
    for snap in load_fund_snapshots():
        out.append(
            FundSection(
                label=snap.label,
                market=snap.market,
                playbook=snap.playbook,
                balance=snap.balance,
                twr_pct=snap.twr_pct,
                max_drawdown_pct=snap.max_drawdown_pct,
                money_gain=snap.money_gain,
                symbols=len(snap.symbols),
                runs=tuple(s for s in sections if s.fund_label == snap.label),
            )
        )
    return out


def equity_chart_b64() -> str | None:
    """계좌 총액 시계열 PNG(base64) — 스냅샷이 없으면 None (메일이 "기록 없음" 한 줄을 적는다).

    Returns:
        `<img src="data:image/png;base64,...">` 에 넣을 문자열. 점이 하나도 없으면 None.
    """
    points = equity.load()
    if not points:
        return None
    return base64.b64encode(render_equity_chart(points)).decode()


async def build_run(
    factory: async_sessionmaker[AsyncSession],
    window: Window,
    *,
    symbol: str,
    when: str,
) -> str | None:
    """판(단일) 이메일 HTML — 그 종목의 섹션만.

    Args:
        factory: DB 세션 팩토리.
        window: 구간.
        symbol: 종목.
        when: 제목줄에 찍을 시각 문자열.

    Returns:
        HTML 본문. 구간에 그 판의 매매가 없으면 None — 빈 표를 꾸미지 않는다.
    """
    sections = await build_sections(factory, window)
    for section in sections:
        if section.symbol == symbol:
            return render_run_email(section, when=when)
    return None


async def build_run_rows(
    factory: async_sessionmaker[AsyncSession],
    window: Window,
    *,
    fund_of: Mapping[str, str] | None = None,
) -> list[RunRow]:
    """대시보드 판 표 — `build_sections` 와 **같은 판·같은 제목줄 수치**, 차트(PNG)만 뺀다 (T220).

    Args:
        factory: DB 세션 팩토리.
        window: 구간.
        fund_of: 판 키 → 펀드 이름. 없으면 "개별 판".

    Returns:
        판 행들 — 손익률 큰 순, 없는 것은 뒤로.

    Note:
        차트 렌더가 판마다 수백 ms 라 화면 폴링에 쓸 수 없다. 숫자는 `meta_for` 를 그대로 쓰므로
        메일과 갈리지 않는다.
        정렬도 같다: 손익률 큰 순, 없는 것은 뒤로.
    """
    rows: list[RunRow] = []
    labels = fund_of or {}
    for run, records in await _live_runs_with_trades(factory, window):
        timeframe = _timeframe_of(run.playbook)
        meta = meta_for(
            symbol=run.symbol,
            playbook=run.playbook_id,
            window=window,
            timeframe=str(timeframe),
            records=records,
            equity_before=None,
        )
        rows.append(
            RunRow(
                key=run.key,
                symbol=run.symbol,
                playbook=run.playbook_id,
                fund_label=labels.get(run.key),
                gain_pct=meta.gain_pct,
                takes=meta.takes,
                stops=meta.stops,
                trades=sum(1 for r in records if r.opened_at is not None),
            )
        )
    rows.sort(key=lambda r: (r.gain_pct is None, -(r.gain_pct or Decimal(0))))
    return rows
