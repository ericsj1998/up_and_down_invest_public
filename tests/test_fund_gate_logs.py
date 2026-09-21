"""펀드 문의 **관측 로그** (2026-09-21) — 메모리에만 있던 것을 로그로 볼 수 있게.

실계좌 점검에서 "문이 세션에 끼워졌나 · 다리 노출이 심겼나 · 사이징 예산이 얼마인가" 를 확인할 길이
없었다(전부 도는 프로세스의 메모리에만 있다). 지키는 것:

- `fund_gate_attached` 는 선언이 아니라 **세션에서 읽은 값**을 적는다 — 문이 없는 세션은
  `ungated` 에 든다.
- `fund_gate_breadth` 는 센 폭과 종목별 마지막 봉 시각을 적는다 — 봉 시각을 모르는 포트(연구 걸음)만
  있으면 아무것도 안 적는다(수만 번 부르는 걸음에 로그를 쏟지 않는다).
- 🔴 로그를 적다 난 예외가 **진입 판정을 막지 않는다**.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.apps.api import rebalancer
from updown.common.domain.instrument import Timeframe
from updown.orchestration.rebalancer import gate as gate_module
from updown.orchestration.rebalancer.gate import SlotGate
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.walkforward import Session

AT = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)


class _Sink:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, *, payload: dict[str, Any]) -> None:
        self.lines.append((event, payload))


def _session(*, gate: object | None, budget: str, legs: dict[str, Decimal]) -> Session:
    made = SimpleNamespace(
        playbooks=(SimpleNamespace(playbook_id="a"), SimpleNamespace(playbook_id="b")),
        ledger=SimpleNamespace(leverage=Decimal(4), margin_budget=Decimal(budget)),
        entry_gate=gate,
        leg_leverage=legs,
    )
    return cast("Session", made)


class TestAttachedLog:
    def test_it_writes_what_the_sessions_actually_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sink = _Sink()
        monkeypatch.setattr(rebalancer, "_events", sink)
        gated = SessionBridge(
            _session(gate=SlotGate(ports={}), budget="62.2471", legs={"a@1": Decimal(4)}),
            breadth_bars=3,
            breadth_frame=Timeframe.H1,
        )
        bare = SessionBridge(_session(gate=None, budget="62.2471", legs={}))
        fund = SimpleNamespace(
            fund_id="fundtest",
            playbook="private_strategy",
            slots=6,
            legs=(),
            coordinator=SimpleNamespace(ports={"BTC_USDT": gated, "ONE_USDT": bare}),
        )
        rebalancer._log_gate(cast("rebalancer.Fund", fund))  # pyright: ignore[reportPrivateUsage]
        ((event, payload),) = sink.lines
        assert event == "fund_gate_attached"
        assert payload["members"]["BTC_USDT"] == {
            "books": ["a", "b"],
            "leverage": "4",
            "budget": "62.25",
            "gate": "SlotGate",
            "leg_exposure": {"a@1": "4"},
            "breadth": "1hx3",
        }
        assert payload["ungated"] == ["ONE_USDT"], "문이 없는 세션은 이름으로 드러나야 한다"

    def test_a_logging_failure_never_breaks_the_fund(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Broken:
            def info(self, event: str, *, payload: dict[str, Any]) -> None:  # noqa: ARG002
                raise RuntimeError("로그가 깨졌다")

        monkeypatch.setattr(rebalancer, "_events", Broken())
        fund = SimpleNamespace(
            fund_id="fundtest",
            playbook="x",
            slots=0,
            legs=(),
            coordinator=SimpleNamespace(ports={}),
        )
        rebalancer._log_gate(cast("rebalancer.Fund", fund))  # pyright: ignore[reportPrivateUsage]


class _Port:
    def __init__(self, breaks: int, seen: datetime | None = None, *, timed: bool = True) -> None:
        self._breaks = breaks
        self._seen = seen
        if timed:
            self.breadth_bar_at = lambda: self._seen

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return Decimal(0)

    def band_breaks(self, at: datetime) -> int:  # noqa: ARG002
        return self._breaks


def _gate(ports: dict[str, _Port]) -> SlotGate:
    return SlotGate(
        ports=ports,
        slots=6,
        notional_cap=Decimal(2),
        notional_fit=True,
        breadth_min=2,
        breadth_cap=Decimal(3),
    )


class TestBreadthLog:
    def test_it_writes_the_count_and_each_siblings_last_bar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sink = _Sink()
        monkeypatch.setattr(gate_module, "_logger", sink)
        late = datetime(2026, 9, 20, 23, 0, tzinfo=UTC)
        ports = {"BTC": _Port(1, AT), "ETH": _Port(1, AT), "SOL": _Port(0, late)}
        assert _gate(ports).grant(AT, Decimal(4)).size == Decimal(4)
        ((event, payload),) = sink.lines
        assert event == "fund_gate_breadth"
        assert (payload["breadth"], payload["cap"]) == (2, "3")
        assert payload["breaks"] == {"BTC": 1, "ETH": 1, "SOL": 0}
        assert payload["last_bar"]["SOL"] == late.isoformat(), "한 칸 늦은 형제가 드러나야 한다"

    def test_research_ports_stay_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sink = _Sink()
        monkeypatch.setattr(gate_module, "_logger", sink)
        ports = {"BTC": _Port(1, timed=False), "ETH": _Port(1, timed=False)}
        _gate(ports).grant(AT, Decimal(4))
        assert sink.lines == []

    def test_a_logging_failure_never_blocks_the_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Broken:
            def info(self, event: str, *, payload: dict[str, Any]) -> None:  # noqa: ARG002
                raise RuntimeError("로그가 깨졌다")

        monkeypatch.setattr(gate_module, "_logger", Broken())
        ports = {"BTC": _Port(1, AT), "ETH": _Port(1, AT)}
        given = _gate(ports).grant(AT, Decimal(4))
        assert given.blocked is None and given.size == Decimal(4)
