"""리포트 한 장을 만들어 보낸다 — 일간 자동과 화면 수동이 **같은 길**을 쓴다 (T35).

길이 둘이면 한쪽만 고쳐진다. 자동은 "지난 24시간 → 기본 수신자", 수동은
"임의 구간 → 임의 수신자"일 뿐 조립은 하나다.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from updown.common.domain.instrument import Market
from updown.common.logging.setup import get_logger
from updown.execution.gateway import OrderGatewayError, order_adapter
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.report.mail import MailSettings, send_mail
from updown.orchestration.report.performance import (
    Performance,
    Window,
    compare,
    load_closed_trades,
    render_funds,
    render_market_breakdown,
    render_text,
    summarize_account_book,
    summarize_records,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from updown.common.config import Settings

_logger = get_logger("orchestration.report.daily")


def window_for(hours: int, *, until: datetime | None = None) -> Window:
    """`until` 에서 `hours` 시간 전까지.

    Args:
        hours: 구간 길이.
        until: 구간 끝. None 이면 지금 (UTC).

    Returns:
        구간.
    """
    end = until or datetime.now(UTC)
    return Window(since=end - timedelta(hours=hours), until=end)


async def _exchange_rows(limit: int = 1000, *, market: str = "GATE") -> list[dict[str, str]] | None:
    """거래소 자금 원장 — 못 읽으면 None (0 으로 꾸미지 않는다).

    Args:
        limit: 최대 행 수.
        market: 어느 거래소의 원장인가 (2026-08-26 — Gate 하드코딩이던 것을 파라미터로.
            바이낸스 매매가 리포트 대조에서 통째로 빠져 있었다).

    Note:
        어댑터는 게이트에서 받는다 (절대 규칙 #0). 자격증명이 없으면 None 이고,
        리포트는 "읽지 못했다"고 쓴다.
    """
    try:
        adapter = order_adapter(MarketDataProvider().adapter_for(Market(market)), user_id="report")
    except (OrderGatewayError, TypeError, ValueError) as exc:
        _logger.warning(
            "report_exchange_unavailable",
            payload={"market": market, "reason": str(exc)[:200]},
        )
        return None
    book = getattr(adapter, "account_book", None)
    if book is None:
        return None
    try:
        return await book(limit=limit)
    except Exception as exc:  # 외부 I/O — 리포트는 매매를 막지 않는다 (§1.2.1)
        _logger.warning("report_exchange_read_failed", payload={"reason": str(exc)[:200]})
        return None


async def build_performance(
    factory: async_sessionmaker[AsyncSession],
    window: Window,
    *,
    exchange_rows: Sequence[dict[str, str]] | None = None,
    fetch_exchange: bool = True,
    market: str | None = None,
) -> Performance:
    """원장 + 거래소를 나란히 모은다.

    Args:
        factory: DB 세션 팩토리.
        window: 구간.
        exchange_rows: 이미 읽은 거래소 행들 (시험·미리보기용). None 이면 읽는다.
        fetch_exchange: 거짓이면 거래소를 안 읽는다.
        market: 거래소 범위. None 이면 전체 — 원장은 전 거래소, 거래소 쪽은 라이브
            거래소들의 원장을 **합쳐** 대조한다 (Gate 만 읽던 시절엔 바이낸스 매매가
            늘 "갈렸다"로 나올 운명이었다).

    Returns:
        리포트 재료.
    """
    records = await load_closed_trades(factory, window, market=market)
    ledger = summarize_records(records, window)
    rows = exchange_rows
    if rows is None and fetch_exchange:
        if market is not None:
            rows = await _exchange_rows(market=market)
        else:
            merged: list[dict[str, str]] = []
            readable = 0
            for name in MarketDataProvider().live_markets():
                part = await _exchange_rows(market=name)
                if part is not None:
                    merged.extend(part)
                    readable += 1
            rows = merged if readable else None
    exchange = summarize_account_book(rows, window) if rows is not None else None
    diverged, note = compare(ledger, exchange)
    return Performance(
        window=window, ledger=ledger, exchange=exchange, diverged=diverged, note=note
    )


def mail_settings_of(settings: Settings) -> MailSettings | None:
    """설정이 다 있으면 발송 설정, 하나라도 없으면 None.

    Args:
        settings: 앱 설정.

    Returns:
        SMTP 발송 설정. 호스트·계정·비밀번호·보내는 주소 중 하나라도 비면 None — 빈 값으로
        조용히 보내지 않는다 (규칙 #8).
    """
    if not (
        settings.smtp_host
        and settings.smtp_user
        and settings.smtp_password
        and settings.notify_from_email
    ):
        return None
    return MailSettings(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_password.get_secret_value(),
        sender=settings.notify_from_email,
    )


async def _market_slices(
    factory: async_sessionmaker[AsyncSession], window: Window
) -> dict[str, Performance]:
    """거래소별 조각 — 전체 리포트의 [거래소별] 절 재료."""
    found: dict[str, Performance] = {}
    for name in MarketDataProvider().live_markets():
        rows = await _exchange_rows(market=name)
        found[name] = await build_performance(
            factory,
            window,
            market=name,
            exchange_rows=rows,
            fetch_exchange=False,
        )
    return found


def _fund_rows() -> list[dict[str, str]]:
    """펀드 파일 스냅샷 — label·market·평가액·TWR (러너 없는 프로세스에서도 읽힌다)."""
    root = Path(os.environ.get("FUNDS_ROOT", "logs/funds"))
    found: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        data = cast("dict[str, Any]", raw)
        twr = cast("dict[str, Any]", data.get("twr") or {})
        found.append(
            {
                "label": str(data.get("label", path.stem)),
                "market": str(data.get("market", "?")),
                "playbook": str(data.get("playbook", "?")),
                "equity": str(twr.get("equity", "?")),
                "twr": str(twr.get("twr", "1")),
            }
        )
    return found


async def send_report(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    window: Window,
    to: Sequence[str] | None = None,
    html: str | None = None,
    market: str | None = None,
) -> tuple[bool, str]:
    """리포트를 만들어 보낸다.

    Args:
        factory: DB 세션 팩토리.
        settings: 앱 설정 (SMTP·기본 수신자).
        window: 구간.
        to: 수신자들. None 이면 `REPORT_TO`.
        html: HTML 본문 (T55). 주면 평문+HTML 멀티파트로 보낸다. None 이면 평문만 —
            평문은 언제나 만든다(대체 본문이자 화면 미리보기).
        market: 거래소 범위 (2026-08-26). None 이면 전체 — 그때만 [거래소별]·[펀드]
            절이 붙는다. 값이 있으면 그 거래소만 담고 제목에 범위를 적는다.

    Returns:
        `(보냈는가, 본문)`. 설정이 없으면 거짓이되 본문은 돌려준다 — 화면 미리보기가 쓴다.
    """
    perf = await build_performance(factory, window, market=market)
    body = render_text(perf)
    if market is None:
        # 전체 리포트에는 거래소별 절 + 펀드 절을 붙인다 (사용자 요구 2026-08-26).
        body += render_market_breakdown(await _market_slices(factory, window))
        body += render_funds(_fund_rows())
    mail = mail_settings_of(settings)
    if mail is None:
        _logger.warning("report_mail_unconfigured", payload={"window": str(window)})
        return False, body
    recipients = (
        list(to)
        if to
        else [name.strip() for name in (settings.report_to or "").split(",") if name.strip()]
    )
    flag = "🔴 대조 불일치 · " if perf.diverged else ""
    scope = f"{market} · " if market else ""
    subject = (
        f"[업앤다운] {scope}{flag}"
        f"{perf.window.since:%m-%d %H:%M}~{perf.window.until:%m-%d %H:%M} UTC"
        f" · 매매 {perf.ledger.trades}건 · 손익률 합 {perf.ledger.gain_sum_pct:+.2f}%"
    )
    return send_mail(mail, to=recipients, subject=subject, body=body, html=html), body
