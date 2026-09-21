"""펀드의 진입 문 — 세션들의 사실을 모아 decision 의 포트폴리오 규칙에 묻는다 (T279 P3).

세션 하나는 다른 종목을 모른다. 동시 보유 상한(§23)과 같은 날 연속 손절 정지(§24)는 **펀드
전체**의 상태라, 펀드가 세션들을 묶을 때 이 문을 만들어 각 세션의 `entry_gate` 에 끼운다.
세션은 사기 직전 `blocks(at)` 만 부른다. 판단은 `decision.portfolio_rules` 가 하고 여기서는
포트에서 숫자를 모아 넘기기만 한다(orchestration 입주 조건).

결정론(규칙 #5): 원장의 기록만 읽는다 — 별도 카운터가 없어 재시작·되읽기에도 어긋나지 않는다.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from updown.common.logging.setup import get_logger
from updown.decision.portfolio_rules import (
    Grant,
    day_halted,
    drawdown_scale,
    notional_room,
    slot_free,
)

_logger = get_logger("rebalancer.gate")


class PositionPort(Protocol):
    """문이 세션에 대고 묻는 두 가지 — 열린 수, 청산 목록. `SessionBridge` 가 구현한다."""

    def open_count(self) -> int:
        """지금 체결돼 보유 중인 매매 수.

        Returns:
            보유 중 기록 수.
        """
        ...

    def exits(self) -> list[tuple[datetime, bool]]:
        """확정된 청산 `(청산 시각, 손절이었나)`.

        Returns:
            시스템 매매의 닫힌 기록.
        """
        ...

    def open_exposure(self) -> Decimal:
        """보유 중 매매의 노출(명목/증거금 · 기록 leverage) 합.

        Returns:
            열린 기록의 leverage 합. 없으면 0.
        """
        ...


@runtime_checkable
class BreadthAware(Protocol):
    """**시장 전체 돌파**를 셀 수 있는 포트 (T289). 없는 포트는 폭에 0 을 보탠다.

    `PositionPort` 와 따로 둔 이유: 폭은 선택 기능이다. 필수 프로토콜에 넣으면 폭을 안 쓰는
    펀드·시험용 포트까지 전부 이 메서드를 가져야 한다(`LeverageAware` 와 같은 패턴).
    """

    def band_breaks(self, at: datetime) -> int:
        """직전 **3개 마감 봉** 안에 종가가 BB(20,2) 상단 밖에서 마감했으면 1, 아니면 0.

        Args:
            at: 진입하려는 봉의 시각(UTC).

        Returns:
            포트가 든 종목 수만큼의 합 — 종목 하나짜리 세션이면 0 또는 1.
        """
        ...


@runtime_checkable
class BreadthTimed(Protocol):
    """폭을 세는 축의 **마지막으로 받은 마감 봉 시각**을 말해 줄 수 있는 포트 — 관측 전용."""

    def breadth_bar_at(self) -> datetime | None:
        """마지막으로 받은 마감 봉의 시작 시각. 폭을 안 세거나 봉이 없으면 None."""
        ...


@dataclass(slots=True)
class SlotGate:
    """P3 진입 문 — 자리(slot)가 다 찼거나 그날 연속 손절 정지면 막는다.

    Attributes:
        ports: `{종목: 포트}` — 펀드가 소유한 세션들. 펀드가 종목을 넣고 빼면 같은 매핑을 본다.
        slots: 동시 보유 상한(0 = 없음).
        halt_after_stops: 같은 날 연속 손절 문턱(0 = 없음).
    """

    ports: Mapping[str, PositionPort]
    slots: int = 0
    halt_after_stops: int = 0
    notional_cap: Decimal | None = None
    """**총 명목 상한**(93차 V2 · 자본 배수). 열린 노출 합 + 새 자리 노출 을 자리 수로 나눈 값
    (= 명목/자본)이 이 값을 넘는 진입은 막는다. None = 없음. 급락 갭에서 계좌가 한 번에 끝나는
    날을 없애는 손잡이(파산 4.2 → 0.5%)."""

    notional_fit: bool = False
    """상한에 걸릴 때 **남은 여유만큼 줄여서** 진입할지 (T286 · A 구성 · 2026-09-19).
    False = 지금까지처럼 그 진입을 통째로 건너뛴다. `notional_cap` 이 있어야 뜻이 있다."""

    min_grant: Decimal = Decimal(0)
    """줄여서 진입할 때의 **허용 하한** — 남은 여유가 이보다 작으면 줄이지 않고 건너뛴다.
    연구 걸음의 `min_room = 0.25 x 선언 배율`. 0 이면 하한 없음.
    ⚠️ 하한이 없으면 거래소 최소 주문에 못 미치는 부스러기 진입이 생긴다."""

    drawdown: Callable[[], Decimal] | None = None
    """지금 **고점 대비 낙폭**(0~1)을 주는 함수 — 펀드가 끼운다. None = 브레이크 없음.
    출처는 펀드 장부의 TWR 지수 낙폭이라 **입출금 중립**이다(입금이 낙폭을 메우지 않는다)."""

    brake_at: Decimal = Decimal(0)
    """낙폭 브레이크 문턱(0.12 = 12% · 144차에서 1% 단위로 쓸어 고른 고원의 가운데). 0 = 없음."""

    brake_scale: Decimal = Decimal(1)
    """브레이크가 걸렸을 때 신규 진입에 곱할 배수(0.5). 걸려도 건너뛰지 않고 **크기만** 줄인다."""

    breadth_min: int = 0
    """**시장 전체 돌파**로 보는 폭의 문턱 (T289 · 177차 등록값 4). 0 = 조건부 상한 없음.

    폭 = 포트들의 `band_breaks(at)` 합 = 직전 3개 마감 봉 안에 상단 밖에서 마감한 종목 수
    (신호를 낸 종목 자신 포함). 진입 시점에 아는 값이다."""

    breadth_cap: Decimal | None = None
    """폭이 `breadth_min` 이상일 때 **그 진입에만** 쓰는 총 명목 상한(177차 등록값 3.0).

    🔴 왜: 여러 종목이 같이 밴드를 뚫는 순간 `notional_cap` 이 차서 뒤쪽 신호가 통째로 버려진다 —
    그런데 그 자리의 건당 EV 가 가장 높다(176차). 크기를 키우거나 혼자 돌파를 거르는 것은 세 창 모두
    ⛔ 였고, **상한만** 올리는 것이 통과했다(177~179차 · 급락 주입 파산 0.07%).
    ⚠️ `notional_cap` 보다 작으면 시장 전체 돌파에서 오히려 조인다 — 선언 파서가 막는다."""

    def blocks(self, at: datetime, exposure: Decimal = Decimal(0)) -> str | None:
        """지금 새 자리를 열면 안 되는 이유 — 없으면 None.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 노출(명목/증거금). 총 명목 상한 판단에만 쓴다.

        Returns:
            `"slots"`(자리 없음) · `"notional"`(총 명목 상한) · `"day_halt"`(그날 정지) ·
            None(열어도 됨).

        Note:
            `grant` 의 사유만 꺼내 쓴다 — 두 벌로 두면 조용히 갈라진다. 줄여서 진입이 켜져 있으면
            상한에 걸려도 **막지 않으므로** 여기서도 None 이다(크기가 줄 뿐이다).
        """
        return self.grant(at, exposure).blocked

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        """이 크기로 열어도 되는지 묻고 **허용 크기**를 돌려준다 (T286).

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 **실제** 노출(명목/증거금 = 배율 x 크기 승수).

        Returns:
            `Grant` — 허용 크기 · 막은 사유 · 줄인 장치.

        Note:
            순서는 연구 걸음 `t279_leaderboard_bn.walk` 과 같다 — **자리·정지로 거르고 → 낙폭
            브레이크로 줄이고 → 총 명목 여유로 자른다**. 브레이크가 먼저인 이유는 줄어든 크기가
            상한에 안 걸릴 수 있기 때문이고, 순서를 뒤집으면 다른 매매법이 된다.

            ⚠️ 호출자는 **실제로 쓸 노출**을 넘겨야 한다. 예전 `blocks` 호출부는 선언 배율을
            넘기고 실제 노출은 뒤에서 따로 계산해 둘이 어긋났다(위험 기반 사이징일 때).
        """
        open_count = sum(port.open_count() for port in self.ports.values())
        if not slot_free(open_count, self.slots):
            return Grant(Decimal(0), "slots")
        exits = [item for port in self.ports.values() for item in port.exits()]
        if day_halted(exits, at, self.halt_after_stops):
            return Grant(Decimal(0), "day_halt")
        want = exposure
        shrunk: list[str] = []
        if self.drawdown is not None and self.brake_at > 0:
            scale = drawdown_scale(self.drawdown(), self.brake_at, self.brake_scale)
            if scale != 1:
                want *= scale
                shrunk.append("brake")
        if exposure > 0 and want <= 0:
            # 브레이크 배수가 0 이면 **흡수 상태**가 된다(진입이 없으면 실현 잔고가 안 움직여
            # 고점을 영영 못 회복한다 · 143차). 선언 파서가 막지만 문에서도 막는다.
            # ⚠️ `exposure > 0` 조건이 있어야 한다 — 크기 없이(0) 묻는 `blocks(at)` 호출을
            #    브레이크로 막으면 안 된다(그 경로는 "자리·정지만 보자" 는 뜻이다).
            return Grant(Decimal(0), "brake")
        held = sum((port.open_exposure() for port in self.ports.values()), Decimal(0))
        room = notional_room(held, self.slots, self._cap_at(at))
        if room is not None and want > room:
            if not self.notional_fit or room <= 0 or room < self.min_grant:
                return Grant(Decimal(0), "notional")
            want = room
            shrunk.append("notional")
        return Grant(want, None, "+".join(shrunk) if shrunk else None)

    def breadth(self, at: datetime) -> int:
        """지금의 **폭** — 직전 3개 마감 봉 안에 상단 밖에서 마감한 종목 수 (T289).

        Args:
            at: 진입하려는 봉의 시각(UTC).

        Returns:
            `BreadthAware` 포트들의 `band_breaks` 합. 폭을 못 세는 포트는 0 을 보탠다.
        """
        return sum(
            port.band_breaks(at) for port in self.ports.values() if isinstance(port, BreadthAware)
        )

    def _cap_at(self, at: datetime) -> Decimal | None:
        """이 진입에 쓸 총 명목 상한 — 시장 전체 돌파면 `breadth_cap`, 아니면 `notional_cap`.

        Note:
            🔴 **센 폭을 적는다** (2026-09-21 · §1-0s 관측 규약). 폭은 형제 세션들이 받아 둔 봉에서
            나오는데, 묻는 순간 형제의 마지막 봉이 아직 안 도착했으면 폭이 **조용히 1 적게** 센다
            (상한이 3x 대신 2x 로 남는다). 얼마나 자주인지 잰 적이 없다 — 종목별 값과 마지막 봉
            시각을 같이 적어 두면 로그만으로 센다. 진입할 때만 불리므로 양은 적다.
        """
        if self.breadth_cap is None or self.breadth_min <= 0 or self.notional_cap is None:
            return self.notional_cap
        counts = {
            name: port.band_breaks(at)
            for name, port in self.ports.items()
            if isinstance(port, BreadthAware)
        }
        wide = sum(counts.values()) >= self.breadth_min
        # 🔴 관측이 진입을 막으면 안 된다 — 로그를 적다 난 예외는 삼킨다(판정은 위에서 끝났다).
        with contextlib.suppress(Exception):
            self._note_breadth(at, counts, wide=wide)
        return self.breadth_cap if wide else self.notional_cap

    def _note_breadth(self, at: datetime, counts: Mapping[str, int], *, wide: bool) -> None:
        """센 폭과 종목별 마지막 봉 시각을 로그로 남긴다 — 관측 전용."""
        if not any(isinstance(port, BreadthTimed) for port in self.ports.values()):
            # 연구 걸음·시험의 포트는 봉 시각을 모른다 — 수만 번 부르는 걸음에 로그를 쏟지 않는다.
            return
        _logger.info(
            "fund_gate_breadth",
            payload={
                "at": at.isoformat(),
                "breadth": sum(counts.values()),
                "min": self.breadth_min,
                "cap": str(self.breadth_cap if wide else self.notional_cap),
                "breaks": counts,
                "last_bar": {
                    name: None if (seen := port.breadth_bar_at()) is None else seen.isoformat()
                    for name, port in self.ports.items()
                    if isinstance(port, BreadthTimed)
                },
            },
        )


@runtime_checkable
class LegSource(Protocol):
    """다리 보기가 세션 다리에 묻는 것 — 귀속 키로 거른 사실들. `SessionBridge` 가 구현한다."""

    def open_count_of(self, leg: str) -> int:
        """그 다리(귀속 키)의 보유 중 매매 수."""
        ...

    def exits_of(self, leg: str) -> list[tuple[datetime, bool]]:
        """그 다리의 확정된 청산 `(청산 시각, 손절이었나)`."""
        ...

    def open_exposure_of(self, leg: str) -> Decimal:
        """그 다리의 보유 중 노출 합."""
        ...

    def band_breaks(self, at: datetime) -> int:
        """폭의 입력 — `BreadthAware` 와 같다."""
        ...


@dataclass(slots=True, frozen=True)
class LegPort:
    """세션 하나를 **다리 하나의 눈으로** 본 포트 (T291).

    한 세션에 두 다리(1H 롱 · 4H 숏)가 실리면 세션 전체의 열린 수·청산·노출은 두 다리가 섞인
    값이다. 다리의 문은 자기 매매만 세야 하므로(측정이 다리마다 자리 6 이었다) 귀속 키로 거른다.

    Attributes:
        source: 원본 세션 다리.
        leg: 이 다리의 귀속 키(`Playbook.attribution` = `TradeRecord.playbook`).
        in_scope: 이 종목이 그 다리의 종목 범위 안인가 — 밖이면 폭에 0 을 보탠다.
    """

    source: LegSource
    leg: str
    in_scope: bool = True

    def open_count(self) -> int:
        """이 다리의 보유 중 매매 수."""
        return self.source.open_count_of(self.leg)

    def exits(self) -> list[tuple[datetime, bool]]:
        """이 다리의 확정된 청산."""
        return self.source.exits_of(self.leg)

    def open_exposure(self) -> Decimal:
        """이 다리의 보유 중 노출 합."""
        return self.source.open_exposure_of(self.leg)

    def band_breaks(self, at: datetime) -> int:
        """폭의 입력 — 다리의 종목 범위 밖이면 0."""
        return self.source.band_breaks(at) if self.in_scope else 0

    def breadth_bar_at(self) -> datetime | None:
        """마지막으로 받은 마감 봉 시각 — 범위 밖이거나 원본이 모르면 None (관측 전용)."""
        source = self.source
        if not self.in_scope or not isinstance(source, BreadthTimed):
            return None
        return source.breadth_bar_at()


@dataclass(slots=True)
class LegPorts(Mapping[str, PositionPort]):
    """펀드의 포트 매핑을 **다리 하나의 눈으로** 본 매핑 — 원본이 바뀌면 같이 바뀐다 (T291).

    Attributes:
        ports: 펀드 조정자의 `{종목: 포트}` — 복사하지 않는다(종목을 넣고 빼면 문도 따라간다).
        leg: 다리의 귀속 키.
        scope: 다리의 종목 범위.
    """

    ports: Mapping[str, object]
    leg: str
    scope: frozenset[str]

    def __getitem__(self, key: str) -> PositionPort:
        """그 종목을 이 다리의 눈으로 본 포트 — 다리별 사실을 못 주는 포트는 없는 것으로 친다."""
        port = self.ports[key]
        if not isinstance(port, LegSource):
            raise KeyError(key)
        return LegPort(port, self.leg, in_scope=key in self.scope)

    def __iter__(self) -> Iterator[str]:
        """다리별 사실을 줄 수 있는 포트의 종목들."""
        return (key for key, port in self.ports.items() if isinstance(port, LegSource))

    def __len__(self) -> int:
        """다리별 사실을 줄 수 있는 포트 수."""
        return sum(1 for _ in self)


@runtime_checkable
class LegAware(Protocol):
    """진입을 **낸 다리**를 알려 주면 그 다리의 문으로 답하는 문 (T291)."""

    def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 묻는다."""
        ...


@dataclass(slots=True)
class LegGate:
    """다리마다 자기 문을 가진 펀드 문 (T291 · 이종 합성 매매법).

    Attributes:
        legs: `{귀속 키: 그 다리의 SlotGate}`.

    Note:
        🔴 **모르는 다리는 막는다**(`"leg"`). 신규 진입은 리스크 증가 행동이고 분류가 불분명하면
        기본값은 보류다(절대 규칙 #8-1). 다리를 모르고 묻는 `grant`/`blocks` 도 같은 이유로 막는다 —
        세션은 `LegAware` 를 알아보고 `grant_for` 로 묻는다.
    """

    legs: Mapping[str, SlotGate]

    def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 묻는다 — 없는 다리면 막는다."""
        gate = self.legs.get(leg)
        if gate is None:
            return Grant(Decimal(0), "leg")
        return gate.grant(at, exposure)

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        """다리를 모르고 물으면 막는다."""
        _ = (at, exposure)
        return Grant(Decimal(0), "leg")

    def blocks(self, at: datetime, exposure: Decimal = Decimal(0)) -> str | None:
        """다리를 모르고 물으면 막는다."""
        _ = (at, exposure)
        return "leg"
