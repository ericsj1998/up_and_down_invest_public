"""시장 전체 돌파일 때만 총 명목 상한을 올린다 (T289 · 177차).

## 무엇을 막으려는 시험인가

A 구성은 여러 종목이 같이 밴드를 뚫는 순간 총 명목 상한 2x 가 차서 **뒤쪽 신호를 통째로 버린다** —
그런데 그 자리의 건당 EV 가 가장 높다(176차 · BOOS +2.88% vs 혼자 +0.76%). 177차는 폭 ≥ 4 에만
상한 3.0 을 써서 세 창 모두 연 ↑ · MDD 동일 · 청산 0 · 급락 주입 파산 0.07% 를 냈다.

이 시험이 지키는 것:
1. 🔴 **기본은 꺼져 있다** — 선언이 없는 펀드(지금 라이브)는 한 글자도 다르게 돌면 안 된다.
2. 폭이 문턱에 **못 미치면** 예전 상한 그대로다 (혼자 돌파에 풀면 93차 V2 가 막은 위험이 돌아온다).
3. 폭을 못 세는 포트는 **0 을 보탠다** — 조용히 예외를 내거나 1 로 세면 안 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from updown.orchestration.rebalancer.gate import PositionPort, SlotGate

Ports = dict[str, PositionPort]

AT = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)


class _Port:
    """종목 하나인 척 — 열린 노출과 '최근 3봉 안에 상단 밖 마감' 여부만 답한다."""

    def __init__(self, exposure: str = "0", breaks: int = 0) -> None:
        self._exposure = Decimal(exposure)
        self._breaks = breaks

    def open_count(self) -> int:
        return 1 if self._exposure > 0 else 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return self._exposure

    def band_breaks(self, at: datetime) -> int:
        _ = at
        return self._breaks


class _BlindPort:
    """폭을 못 세는 포트 — `band_breaks` 가 없다."""

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return Decimal(0)


def _gate(ports: Ports, *, breadth_min: int = 4, breadth_cap: str | None = "3") -> SlotGate:
    return SlotGate(
        ports=ports,
        slots=6,
        notional_cap=Decimal(2),  # 방 = 2 x 6 = 12
        notional_fit=True,
        min_grant=Decimal(1),
        breadth_min=breadth_min,
        breadth_cap=Decimal(breadth_cap) if breadth_cap is not None else None,
    )


def _full(breaks: int) -> Ports:
    """세 종목이 4배씩(합 12 = 상한 2x 꽉 참) 들고 있고, `breaks` 개 종목이 밴드를 뚫은 상태."""
    ports: Ports = {f"H{i}": _Port("4", 1 if i < breaks else 0) for i in range(3)}
    ports |= {f"E{i}": _Port("0", 1 if i + 3 < breaks else 0) for i in range(3)}
    return ports


class TestConditionalCap:
    def test_alone_breakout_keeps_the_old_cap(self) -> None:
        # 폭 1 — 상한 2x 가 찼으므로 예전처럼 막는다.
        assert _gate(_full(1)).grant(AT, Decimal(4)).blocked == "notional"

    def test_market_wide_breakout_gets_the_higher_cap(self) -> None:
        # 폭 4 — 상한 3x(방 18)라 남은 방 6 안에 4배가 그대로 들어간다.
        got = _gate(_full(4)).grant(AT, Decimal(4))
        assert got.blocked is None
        assert got.size == Decimal(4)
        assert got.shrunk is None

    def test_higher_cap_still_binds(self) -> None:
        """올린 상한도 **상한이다** — 넘치면 줄여서 받는다(통째로 푸는 V2f 와 다른 점)."""
        ports: Ports = {f"H{i}": _Port("4", 1) for i in range(4)}  # 합 16 · 폭 4 · 방 18 - 16 = 2
        got = _gate(ports).grant(AT, Decimal(6))
        assert got.size == Decimal(2)
        assert got.shrunk == "notional"

    def test_threshold_is_inclusive(self) -> None:
        assert _gate(_full(3)).grant(AT, Decimal(4)).blocked == "notional"
        assert _gate(_full(4)).grant(AT, Decimal(4)).blocked is None


class TestOffByDefault:
    """🔴 선언이 없는 펀드(지금 라이브 A 구성)는 예전과 **똑같이** 돌아야 한다."""

    def test_no_breadth_cap_means_old_behavior(self) -> None:
        gate = _gate(_full(6), breadth_cap=None)
        assert gate.grant(AT, Decimal(4)).blocked == "notional"

    def test_zero_threshold_means_off(self) -> None:
        gate = _gate(_full(6), breadth_min=0)
        assert gate.grant(AT, Decimal(4)).blocked == "notional"

    def test_defaults_are_off(self) -> None:
        gate = SlotGate(ports=_full(6), slots=6, notional_cap=Decimal(2))
        assert gate.breadth_min == 0
        assert gate.breadth_cap is None
        assert gate.grant(AT, Decimal(4)).blocked == "notional"


class TestBlindPorts:
    def test_ports_that_cannot_count_add_zero(self) -> None:
        ports: Ports = {f"H{i}": _Port("4", 1) for i in range(3)}
        ports["X"] = _BlindPort()
        gate = _gate(ports)
        assert gate.breadth(AT) == 3
        assert gate.grant(AT, Decimal(4)).blocked == "notional", "폭 3 은 문턱 4 에 못 미친다"
