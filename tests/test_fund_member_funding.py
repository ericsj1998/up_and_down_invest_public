"""펀드 판은 계좌 예산 검사(`check_funding`)를 안 쓴다 (2026-09-26 · 실계좌 전 판 진입 정지 사고).

2026-09-25 17:30 UTC: 펀드 판 40개의 저장 예산 합 417.31(판을 만든 날의 몫) > 계좌 406.82 →
허용 2% 를 넘어 40판 전부 `funded=False` — 새 진입이 없으니 스스로 풀리지도 않았다.
단독 판은 예전처럼 잰다.
"""

from __future__ import annotations

import asyncio
import dataclasses
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from updown.orchestration.walkforward.live_runner import LiveRunner
from updown.orchestration.walkforward.session import Session


class _Orders:
    """계좌 총액 = 가용 + 포지션 증거금 = 406.82."""

    async def get_balance(self) -> Any:
        return SimpleNamespace(cash=Decimal("300.00"))

    async def account_margin(self) -> Decimal:
        return Decimal("106.82")


class _Store:
    """판 40개 x 저장 예산 10.43275 = 417.31."""

    async def open_runs(self, *, live: bool) -> list[dict[str, Any]]:
        assert live
        return [{"margin": "10.43275"} for _ in range(40)]


def _quiet(*_a: object) -> None:
    return None


def runner(fund_name: str | None, *, funded: bool = True) -> Any:
    events: list[tuple[str, str]] = []

    def record(level: str) -> Any:
        def log(event: str, **_kw: Any) -> None:
            events.append((level, event))

        return log

    return SimpleNamespace(
        observe_only=False,
        _store=_Store(),
        _orders=_Orders(),
        _session=SimpleNamespace(fund_name=fund_name, funded=funded),
        short_by=None,
        _log=SimpleNamespace(error=record("error"), info=record("info"), warning=record("warning")),
        _fired=_quiet,
        events=events,
    )


def check(r: Any) -> None:
    asyncio.run(cast("Any", LiveRunner).check_funding(r))


class TestFundBoardsAreExempt:
    def test_the_2026_09_25_numbers_block_a_standalone_board(self) -> None:
        r = runner(None)
        check(r)
        assert r._session.funded is False
        assert r.short_by == "10.49"
        assert ("error", "live_underfunded") in r.events

    def test_the_same_numbers_leave_a_fund_board_open(self) -> None:
        r = runner("private_strategy")
        check(r)
        assert r._session.funded is True and r.short_by is None
        assert r.events == []

    def test_a_blocked_fund_board_is_reopened(self) -> None:
        r = runner("private_strategy", funded=False)
        check(r)
        assert r._session.funded is True
        assert r.events == [("info", "live_funded_again")]


def test_sessions_start_as_standalone() -> None:
    """기본값은 단독 판 — 펀드 이름은 API 의 판 시작만 꽂는다."""
    defaults = {f.name: f.default for f in dataclasses.fields(Session)}
    assert defaults["fund_name"] is None
