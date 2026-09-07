"""이메일 성과 리포트 API (T35) — 미리보기와 수동 발송.

🔴 **보내기 전에 본다.** 외부로 나가는 평문이라 `preview` 가 먼저고, `send` 는 화면이
확인 단계를 거친 뒤 부른다. 수신자를 안 주면 `.env` 의 기본 수신자다.

⚠️ 권한: 지금은 누구나 부를 수 있다. [T37](../../../../docs/planning/tasks/T37_roles_and_admin.md)
이 서면 **운용자 이상**으로 좁힌다 — 그 전까지는 로컬 전용 전제다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.config import Settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.logging.setup import get_logger
from updown.orchestration.report import dashboard as dash
from updown.orchestration.report import equity
from updown.orchestration.report.compose import (
    build_console,
    build_run,
    build_run_rows,
    fund_labels_by_run,
)
from updown.orchestration.report.daily import (
    _fund_rows,  # pyright: ignore[reportPrivateUsage] — 미리보기=발송 같은 길 (T35)
    _market_slices,  # pyright: ignore[reportPrivateUsage]
    build_performance,
    mail_settings_of,
    send_report,
    window_for,
)
from updown.orchestration.report.funds import load_fund_snapshots
from updown.orchestration.report.html import AccountSummary
from updown.orchestration.report.performance import (
    Performance,
    Window,
    load_closed_trades,
    render_funds,
    render_market_breakdown,
    render_text,
)

router = APIRouter(prefix="/report", tags=["report"])

KST = ZoneInfo("Asia/Seoul")
"""표시·발송 시각의 기준 — 저장은 UTC 다 (절대 규칙 #7)."""

_logger = get_logger("apps.api.report")


def _when(window: Window) -> str:
    """헤더 시각 문자열 — 지금(KST) · 구간 길이. 표시만 KST (규칙 #7)."""
    kst = datetime.now(UTC) + timedelta(hours=9)
    hours = round((window.until - window.since).total_seconds() / 3600)
    return f"{kst:%Y-%m-%d %H:%M} KST · 지난 {hours}시간"


async def _account_summary(perf: Performance) -> AccountSummary:
    """계좌 전체 스냅샷 — 콘솔 총 자산과 같은 공식. 못 읽은 값은 None (규칙 #8).

    Note:
        🔴 **금고·잔액은 살아 있는 러너/거래소에만 있다.** 그래서 여기(apps)서 모아
        아래(orchestration)로 **주입**한다 — 경계를 지킨다. 스케줄러(engine)가 부르면
        러너가 없어 금고는 None 이 된다: 지어내지 않고 "—" 로 나간다.
    """
    # 순환 import 를 피해 함수 안에서 늦게 부른다 (두 라우터가 서로를 안 물게).
    from updown.apps.api.exchange import (
        _all_live,  # pyright: ignore[reportPrivateUsage]
        _orders_adapter,  # pyright: ignore[reportPrivateUsage]
        _tracked,  # pyright: ignore[reportPrivateUsage]
    )
    from updown.apps.api.walkforward import SESSIONS

    available: Decimal | None = None
    locked: Decimal | None = None
    unrealized: Decimal | None = None
    try:
        orders = _orders_adapter()
        balance = await orders.get_balance()
        available = Decimal(str(balance.cash))
        tracked = await _tracked(orders, "GATE")
        positions = (await _all_live(orders, "GATE", tracked)).get("positions", [])
        locked = sum((Decimal(str(p.get("margin", "0"))) for p in positions), Decimal(0))
        unrealized = sum(
            (Decimal(str(p.get("unrealised_pnl", "0"))) for p in positions), Decimal(0)
        )
    except Exception as exc:  # 외부 I/O — 리포트는 매매를 막지 않는다 (§1.2.1)
        _logger.warning("report_account_unavailable", payload={"reason": str(exc)[:200]})

    vault: Decimal | None = None
    try:
        books = [live.session.ledger for live in SESSIONS.values()]
        vault = sum((b.reserved - b.withdrawn for b in books), Decimal(0))
    except Exception as exc:
        _logger.warning("report_vault_unavailable", payload={"reason": str(exc)[:200]})

    total = None if available is None else available + (locked or Decimal(0))
    led = perf.ledger
    return AccountSummary(
        total=total,
        available=available,
        locked=locked,
        realized=perf.exchange.pnl if perf.exchange is not None else None,
        unrealized=unrealized,
        vault=vault,
        win_rate=led.win_rate,
        trades=led.trades,
        runs=len(SESSIONS),
        diverged=perf.diverged,
        note=perf.note,
    )


_factory: async_sessionmaker[AsyncSession] | None = None


def _sessions() -> async_sessionmaker[AsyncSession]:
    """세션 팩토리 — 첫 호출에 만든다 (요청마다 엔진을 만들지 않는다)."""
    global _factory
    if _factory is None:
        _factory = create_session_factory(create_engine(Settings().database_url))  # type: ignore[call-arg]
    return _factory


def _window(payload: dict[str, Any]) -> Window:
    """`hours` 또는 `since/until`(ISO, UTC) 로 구간을 만든다."""
    since, until = payload.get("since"), payload.get("until")
    if since and until:
        start = datetime.fromisoformat(str(since))
        end = datetime.fromisoformat(str(until))
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(400, "since/until 은 tz 가 있는 ISO8601 이어야 한다 (UTC)")
        return Window(since=start.astimezone(UTC), until=end.astimezone(UTC))
    hours = int(payload.get("hours", 24))
    if hours <= 0 or hours > 24 * 31:
        raise HTTPException(400, f"hours 는 1~744 — 받은 값 {hours}")
    return window_for(hours)


async def compose_report(
    window: Window, *, market: str | None = None, symbol: object | None = None
) -> tuple[Performance, str, str | None]:
    """리포트 한 벌 — **평문과 HTML 을 같이** 만든다.

    Args:
        window: 구간.
        market: 거래소 범위. None 이면 전체 (그때만 [거래소별]·[펀드] 절이 붙는다).
        symbol: 종목 하나만 볼 때.

    Returns:
        `(성과, 평문, HTML)`. HTML 은 못 만드는 조합이면 None.

    Note:
        🔴 **미리보기·수동 발송·자동 발송이 이 함수 하나를 쓴다** (T35 원칙). 예전에는
        셋이 각자 조립했고, 그래서 갈렸다:

            화면 미리보기   평문만
            수동 발송       평문 + HTML(차트·주문표·계좌)
            자동 발송       평문만          ← 사용자가 받은 것

        같은 길이 아니면 *"메일에 뜬 숫자와 화면 숫자가 다르다"* 가 언젠가 난다.

        ⚠️ 거래소 범위 발송은 **평문만**이다 — HTML 콘솔 조립은 전체 기준이라 섞으면
        숫자가 두 말을 한다. 필요해지면 HTML 도 범위를 받게 확장한다.

        ⭐ 계좌 요약은 **살아 있는 러너**에서 온다 (`_account_summary`). 그래서 이 조립은
        API 프로세스에서만 성립한다 — 엔진이 따로 조립하면 그 칸이 빈다.
    """
    perf = await build_performance(_sessions(), window, market=market)
    body = render_text(perf)
    if market is None:
        body += render_market_breakdown(await _market_slices(_sessions(), window))
        body += render_funds(_fund_rows())
    when = _when(window)
    if symbol:
        return perf, body, await build_run(_sessions(), window, symbol=str(symbol), when=when)
    if market is None:
        account = await _account_summary(perf)
        return perf, body, await build_console(_sessions(), window, account, when=when)
    return perf, body, None


async def daily_report_loop() -> None:
    """매일 지정 시각(KST)에 **차트가 붙은** 리포트를 보낸다.

    Note:
        🔴 **엔진이 아니라 여기서 돈다** (사용자 확정 2026-08-30). 엔진 스케줄러가
        보내던 것은 `html` 을 안 넘겨 **평문만** 나갔다 — 기능이 없어진 것이 아니라
        자동 경로에 배선이 안 돼 있었다.

        ⇒ 계좌 요약이 **살아 있는 러너**에 있으므로, 조립도 그 러너가 있는 프로세스에서
        해야 한다. 엔진은 다른 프로세스라 그 값을 못 본다 — 거기서 조립하면 요청하신
        *"손익 등등"* 의 절반이 빈칸이 된다.

        ⛔ **엔진 쪽 잡은 등록하지 않는다** (`register_daily_report`). 둘 다 돌면 매일
        두 통이 가고, 한 통은 옛 형식이다.

        ⚠️ 실패해도 다시 잔다 — 리포트는 리스크 증가 행동이 아니므로 매매를 막지
        않는다 (§1.2.1). 이력은 `report_sends.jsonl` 에 남는다.
    """
    settings = Settings()  # type: ignore[call-arg]
    if mail_settings_of(settings) is None or not settings.report_to:
        _logger.warning(
            "daily_report_not_scheduled",
            payload={
                "reason": "SMTP 또는 REPORT_TO 설정이 없다 — .env 를 채우면 다음 기동에 붙는다"
            },
        )
        return
    _logger.info(
        "daily_report_scheduled",
        payload={"hour_kst": settings.report_hour_kst, "to": settings.report_to, "html": True},
    )
    while True:
        await asyncio.sleep(_until_next(settings.report_hour_kst))
        try:
            window = window_for(24)
            _perf, _body, html = await compose_report(window)
            sent, _again = await send_report(_sessions(), settings, window=window, html=html)
            _logger.info(
                "daily_report_job_finished", payload={"sent": sent, "html": html is not None}
            )
        except Exception as exc:  # 리포트 실패가 매매를 막지 않는다 (§1.2.1)
            _logger.error("daily_report_failed", payload={"error": str(exc)[:200]})


def _until_next(hour_kst: int, *, now: datetime | None = None) -> float:
    """다음 발송까지 남은 초 — **KST 기준 `hour_kst`:05**.

    Args:
        hour_kst: 보내는 시각 (KST, 0~23).
        now: 시험용 현재 시각 (UTC aware).

    Returns:
        남은 초. 이미 지났으면 내일 그 시각까지.

    Note:
        🔴 **KST 로 받고 tz 를 명시한다** (절대 규칙 #7). UTC 시(hour)로 바꿔 적어 두면
        사람이 읽을 때 틀린다.
    """
    clock = (now or datetime.now(UTC)).astimezone(KST)
    target = clock.replace(hour=hour_kst % 24, minute=5, second=0, microsecond=0)
    if target <= clock:
        target += timedelta(days=1)
    return (target - clock).total_seconds()


@router.get("/preview")
async def preview(
    hours: int = 24, market: str | None = None, symbol: str | None = None
) -> dict[str, Any]:
    """지난 `hours` 시간의 리포트 본문 — 보내지 않는다.

    Args:
        hours: 구간 길이 (1~744).
        market: 거래소로 좁힌다. 없으면 전체.
        symbol: 판 하나로 좁힌다.

    Returns:
        구간 · 본문(평문·HTML) · 기본 수신자 — 발송과 같은 재료.

    Note:
        발송과 **같은 본문**이어야 한다 (T35 — 자동·수동·미리보기가 같은 길).
        `market` 없으면 전체 = [거래소별]·[펀드] 절 포함 (2026-08-26).
        `symbol` 을 주면 그 판만 — 발송의 `symbol` 과 같은 길 (T220 · 대시보드 메일 카드의
        판 고르기).
    """
    window = _window({"hours": hours})
    scope = market.strip().upper() if market else None
    perf, body, html = await compose_report(
        window, market=scope, symbol=symbol.strip().upper() if symbol else None
    )
    settings = Settings()  # type: ignore[call-arg]
    return {
        "window": {"since": window.since.isoformat(), "until": window.until.isoformat()},
        "body": body,
        # 🔴 **차트가 붙은 그 본문을 화면도 본다** (사용자 요구 2026-08-30). 예전에는
        #    미리보기가 평문만 돌려줘서, 화면이 메일과 **다른 것**을 보여 주고 있었다 —
        #    이 파일 머리말이 *"자동·수동·미리보기가 같은 길"* 이라고 적어 둔 그 원칙이
        #    깨져 있었다.
        "html": html,
        "diverged": perf.diverged,
        "trades": perf.ledger.trades,
        "configured": mail_settings_of(settings) is not None,
        "default_to": [
            name.strip() for name in (settings.report_to or "").split(",") if name.strip()
        ],
    }


@router.get("/vault")
async def vault_balance() -> dict[str, Any]:
    """금고 **잔고** — 살아 있는 판들의 reserved 빼기 withdrawn 합 (T55).

    Returns:
        `{vault, runs}`. 못 읽으면 `vault` 는 None.

    Note:
        ⚠️ `Vault`(금고 **한도**) 카드와 다르다 — 저것은 재충전 상한(설정값)이고 이것은
        수익선 넘겨 실제로 **빼 둔 돈**이다. 못 읽으면 None (규칙 #8 · 0 으로 안 꾸민다).
    """
    from updown.apps.api.walkforward import SESSIONS

    try:
        books = [live.session.ledger for live in SESSIONS.values()]
        total = sum((b.reserved - b.withdrawn for b in books), Decimal(0))
        return {"vault": str(total), "runs": len(books)}
    except Exception as exc:  # 외부 상태 — 리포트는 매매를 막지 않는다 (§1.2.1)
        _logger.warning("report_vault_endpoint_failed", payload={"reason": str(exc)[:200]})
        return {"vault": None, "runs": 0}


@router.get("/dashboard")
async def dashboard(hours: int = 24) -> dict[str, Any]:
    """리포트 **대시보드** JSON — 한 화면 (T220 2단계 · 사용자 2026-09-05).

    Args:
        hours: 구간 길이 (1~744).

    Returns:
        원장·거래소 집계 · 계좌(못 읽으면 None) · 판 표 · 누적 곡선 · 발송 설정.

    Note:
        🔴 메일과 **같은 숫자**다: `build_performance`(원장·거래소) · `_account_summary`(계좌) ·
        `meta_for`(판 제목줄)를
        그대로 쓴다. 차트 PNG 만 만들지 않는다 — 화면이 숫자로 스스로 그린다.
        `hours` 는 1~744 (미리보기와 같은 범위).
    """
    window = _window({"hours": hours})
    perf = await build_performance(_sessions(), window)
    account: AccountSummary | None
    try:
        account = await _account_summary(perf)
    except Exception as exc:  # 외부 상태 — 계좌 칸만 비운다 (규칙 #8 · 0 으로 안 꾸민다)
        _logger.warning("report_dashboard_account_failed", payload={"reason": str(exc)[:200]})
        account = None
    rows = await build_run_rows(_sessions(), window, fund_of=fund_labels_by_run())
    curve = dash.gain_curve(
        await load_closed_trades(_sessions(), window), {r.key: r.symbol for r in rows}
    )
    settings = Settings()  # type: ignore[call-arg]
    return dash.payload(
        perf,
        account,
        rows,
        curve,
        configured=mail_settings_of(settings) is not None,
        default_to=[name.strip() for name in (settings.report_to or "").split(",") if name.strip()],
        funds=dash.fund_rows(load_fund_snapshots()),
        equity=equity.monthly(equity.load()),
    )


async def snapshot_equity() -> equity.EquityPoint:
    """지금 계좌 총액·펀드 잔고를 한 점 적는다 — 월별 자산 꺾은선의 원천 (사용자 2026-09-07).

    Returns:
        적은 점.

    Note:
        총액은 리포트 계좌 카드와 같은 정의(`_account_summary`: 가용 + 포지션 증거금). 못 읽은
        값은 None 으로 적는다 — 0 으로 꾸미지 않는다 (규칙 #8). 펀드 잔고는 메모리(`FUNDS`)의
        TWR 원장이다.
    """
    from updown.apps.api.rebalancer import FUNDS

    perf = await build_performance(_sessions(), window_for(24))
    account = await _account_summary(perf)
    point = equity.EquityPoint(
        at=datetime.now(UTC),
        total=account.total,
        available=account.available,
        locked=account.locked,
        unrealized=account.unrealized,
        vault=account.vault,
        funds={fid: fund.coordinator.engine.balance for fid, fund in FUNDS.items()},
    )
    equity.record(point)
    _logger.info(
        "equity_snapshot_recorded",
        payload={
            "total": None if point.total is None else str(point.total),
            "funds": len(point.funds),
        },
    )
    return point


async def equity_snapshot_loop() -> None:
    """매일 00:05 KST 자산 스냅샷 한 줄 — 기동 직후 오늘 것이 없으면 바로 하나 적는다.

    Note:
        리포트 루프와 같은 프로세스(거래 리더)에서 돈다 — 계좌 요약이 살아 있는 러너에 있기
        때문이다.
        실패해도 다음 날 다시 한다 — 스냅샷은 리스크 증가 행동이 아니다 (§1.2.1).
    """
    while True:
        try:
            if not equity.recorded_today(equity.load(), datetime.now(UTC)):
                await snapshot_equity()
        except Exception as exc:
            _logger.warning("equity_snapshot_failed", payload={"error": str(exc)[:200]})
        await asyncio.sleep(_until_next(0))


@router.post("/send")
async def send(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """리포트를 보낸다 — `{hours | since+until, to?: [..], symbol?}`.

    Args:
        payload: 구간(`hours` 또는 `since`+`until`) · 수신자 · 판.

    Returns:
        `{ok, ...}` — 실패도 200 으로 `ok: false`.

    Note:
        🔴 실패는 500 이 아니라 `ok: false` 다 — 이력(`logs/report_sends.jsonl`)에
        남고, 화면이 그 사실을 말한다 (규칙 #8).

        ⭐ **`symbol` 이 있으면 그 판만** (판 이메일), 없으면 **전체 취합**(콘솔 이메일).
        HTML 을 못 만들면(그 판 매매가 구간에 없음) 평문만 나간다 — 조용히 안 죽는다.
    """
    window = _window(payload)
    raw_to = payload.get("to")
    recipients = (
        [str(name).strip() for name in cast("list[object]", raw_to)]
        if isinstance(raw_to, list)
        else None
    )
    symbol = payload.get("symbol")
    # ⭐ 거래소 범위 (사용자 요구 2026-08-26) — "전체 / GATE 전체 / BINANCE 전체".
    market = str(payload["market"]).strip().upper() if payload.get("market") else None
    _perf, _body, html = await compose_report(window, market=market, symbol=symbol)
    ok, body = await send_report(
        _sessions(),
        Settings(),  # type: ignore[call-arg]
        window=window,
        to=recipients,
        html=html,
        market=market,
    )
    return {"ok": ok, "body": body, "html": html is not None}
