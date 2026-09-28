"""빈 판의 점검 주기 — CPU 예산(2026-09-30).

40판이 60초마다 거래소에 포지션 · 주문 · 봉을 물어 1 GB 버스트 서버의 크레딧을 갉아먹었다.
열린 매매 · 걸린 표 · 감사 이상이 없는 판은 `PROBE_IDLE_TICK` 으로 잔다 — 늦추는 것이지 끄는
것이 아니다.
"""

from __future__ import annotations

import inspect

from test_live_incident_20260820 import (  # pyright: ignore[reportPrivateUsage]
    Exchange,
    at,
    ledger_with,
    runner_for,
    trade,
)
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.live_runner import LiveRunner


def _empty_runner() -> LiveRunner:
    made = runner_for(ledger_with(), Exchange())
    session = made._session  # pyright: ignore[reportPrivateUsage]
    session.waiting_trade = None  # type: ignore[attr-defined]
    session.positions = ()  # type: ignore[attr-defined]
    made.findings = []
    return made


class TestProbeBusy:
    def test_empty_board_is_idle(self) -> None:
        assert _empty_runner()._probe_busy() is False  # pyright: ignore[reportPrivateUsage]

    def test_open_trade_is_busy(self) -> None:
        made = _empty_runner()
        made._session.ledger.records.append(  # pyright: ignore[reportPrivateUsage]
            trade(opened_at=at("2026-08-20T00:01:00Z"))
        )
        assert made._session.ledger.records[0].outcome is Outcome.OPEN  # pyright: ignore[reportPrivateUsage]
        assert made._probe_busy() is True  # pyright: ignore[reportPrivateUsage]

    def test_waiting_order_is_busy(self) -> None:
        """대기 지정가는 봉 사이에 채워진다(2026-09-06 사고) — 빈 원장이어도 자주 본다."""
        made = _empty_runner()
        made._session.waiting_trade = trade(  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
            outcome=Outcome.PENDING
        )
        assert made._probe_busy() is True  # pyright: ignore[reportPrivateUsage]

    def test_audit_finding_is_busy(self) -> None:
        """감사가 이상(고아 포지션 · 무방비)을 남겼으면 고칠 때까지 자주 돈다."""
        made = _empty_runner()
        made.findings = [{"code": "stop_missing", "detail": ""}]
        assert made._probe_busy() is True  # pyright: ignore[reportPrivateUsage]

    def test_idle_tick_is_longer_and_only_delays(self) -> None:
        assert live_runner.PROBE_IDLE_TICK > live_runner.PROBE_TICK
        source = inspect.getsource(LiveRunner._keep_probing)  # pyright: ignore[reportPrivateUsage]
        assert "PROBE_TICK if self._probe_busy() else PROBE_IDLE_TICK" in source
        # 늦출 뿐이다 — 점검 본문(대조 · 감사 · 무방비 고치기)은 그대로 돈다.
        assert "await self.reconcile()" in source and "_run_audit" in source
