"""펀드의 **다리**(leg) — 한 펀드 안에서 따로 굴리는 매매법 하나의 계좌 층 (T291).

Gate 무기한은 종목당 포지션이 하나라, 시간축·방향이 다른 두 매매법(1H 돌파 롱 · 4H 삼각수렴 숏)을
같은 계좌에서 같이 돌리려면 **한 펀드 · 한 세션**이어야 한다. 그런데 측정은 다리마다 자기 자리(6)와
자기 상한으로 쟀다 — 자리와 총 명목 상한을 나눠 쓰면 숏이 열린 동안 롱 진입이 깎여 다른
매매법이 된다(이종 합성 매매법).

이 모듈은 선언(`Playbook.split_legs` + 구성원 선언 + 종목 범위)에서 다리들을 만들고, 다리마다 문을
세운다. 판단은 없다 — 선언을 읽어 `SlotGate` 를 조립할 뿐이다(orchestration 입주 조건).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from updown.analysis.playbook.types import BreadthCap, DrawdownBrake, Playbook
from updown.orchestration.rebalancer.gate import LegGate, LegPorts, SlotGate


class LegError(ValueError):
    """다리 선언이 서로 안 맞는다 — 펀드를 만들기 전에 터뜨린다 (절대 규칙 #8)."""


@dataclass(slots=True, frozen=True)
class FundLeg:
    """펀드의 다리 하나 — 구성원 매매법의 계좌 층 + 종목 범위.

    Attributes:
        playbook: 구성원 매매법 id (세션에 실을 이름).
        attribution: 귀속 키(`Playbook.attribution` = `TradeRecord.playbook`) — 문이 자기 매매를
            세는 열쇠.
        symbols: 이 다리가 진입하는 종목(펀드 종목과의 교집합).
        leverage: 이 다리의 거래소(격리) 배율 — 세션 배율은 그 종목에 실린 다리들의 최댓값이다.
        exposure: 이 다리 진입의 노출(명목/자리 예산). 선언(`leg_exposure`)이 없으면 `leverage`.
        timeframe: 진입 축 — 폭(`breadth_cap`)을 세는 축.
        slots: 동시 보유 상한.
        halt_after_stops: 같은 날 연속 손절 정지 문턱. 0 = 없음.
        notional_cap: 총 명목 상한(자본 배수). None = 없음.
        notional_fit: 상한에 걸리면 줄여서 진입.
        drawdown_brake: 낙폭 브레이크. None = 없음.
        breadth_cap: 조건부 총 명목 상한. None = 없음.
        halt_dd_at: 펀드 낙폭이 이 값 이상이면 이 다리는 새로 안 든다(T304 #1). None = 없음.
    """

    playbook: str
    attribution: str
    symbols: tuple[str, ...]
    leverage: Decimal
    exposure: Decimal
    timeframe: str
    slots: int
    halt_after_stops: int = 0
    notional_cap: Decimal | None = None
    notional_fit: bool = False
    drawdown_brake: DrawdownBrake | None = None
    breadth_cap: BreadthCap | None = None
    halt_dd_at: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        """저장용 딕셔너리 — 돌던 펀드의 다리는 선언이 바뀌어도 안 바뀐다(저장본이 이긴다)."""
        return {
            "playbook": self.playbook,
            "attribution": self.attribution,
            "symbols": list(self.symbols),
            "leverage": str(self.leverage),
            "exposure": str(self.exposure),
            "timeframe": self.timeframe,
            "slots": self.slots,
            "halt_after_stops": self.halt_after_stops,
            "notional_cap": None if self.notional_cap is None else str(self.notional_cap),
            "notional_fit": self.notional_fit,
            "drawdown_brake": (
                None
                if self.drawdown_brake is None
                else {"at": str(self.drawdown_brake.at), "scale": str(self.drawdown_brake.scale)}
            ),
            "breadth_cap": (
                None
                if self.breadth_cap is None
                else {
                    "min": self.breadth_cap.min,
                    "cap": str(self.breadth_cap.cap),
                    "bars": self.breadth_cap.bars,
                }
            ),
            "halt_dd_at": None if self.halt_dd_at is None else str(self.halt_dd_at),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FundLeg:
        """저장본에서 되살린다.

        Args:
            data: `to_dict` 가 쓴 딕셔너리.

        Returns:
            다리.
        """
        raw_cap = data.get("notional_cap")
        raw_brake = data.get("drawdown_brake")
        raw_breadth = data.get("breadth_cap")
        raw_halt = data.get("halt_dd_at")  # 1.17.0 앞 저장본엔 없다 — 없으면 끔(그때 돌던 그대로)
        brake = None
        if isinstance(raw_brake, Mapping):
            body = cast("Mapping[str, Any]", raw_brake)
            brake = DrawdownBrake(at=Decimal(str(body["at"])), scale=Decimal(str(body["scale"])))
        breadth = None
        if isinstance(raw_breadth, Mapping):
            body = cast("Mapping[str, Any]", raw_breadth)
            breadth = BreadthCap(
                min=int(body["min"]), cap=Decimal(str(body["cap"])), bars=int(body.get("bars", 3))
            )
        return cls(
            playbook=str(data["playbook"]),
            attribution=str(data["attribution"]),
            symbols=tuple(str(item) for item in cast("Sequence[object]", data["symbols"])),
            leverage=Decimal(str(data["leverage"])),
            exposure=Decimal(str(data.get("exposure") or data["leverage"])),
            timeframe=str(data["timeframe"]),
            slots=int(data["slots"]),
            halt_after_stops=int(data.get("halt_after_stops", 0) or 0),
            notional_cap=None if raw_cap in (None, "") else Decimal(str(raw_cap)),
            notional_fit=bool(data.get("notional_fit", False)),
            drawdown_brake=brake,
            breadth_cap=breadth,
            halt_dd_at=None if raw_halt in (None, "") else Decimal(str(raw_halt)),
        )


def declared_legs(
    wrapper: Playbook,
    books: Sequence[Playbook],
    scopes: Mapping[str, Sequence[str]],
    members: Sequence[str],
) -> tuple[FundLeg, ...]:
    """묶음 선언에서 다리들을 만든다 — `split_legs` 가 꺼져 있으면 빈 튜플(지금까지의 펀드).

    Args:
        wrapper: 펀드가 고른 매매법(묶음 항목).
        books: 선언된 매매법 전부.
        scopes: `{구성원 id: 종목들}` — `config/baskets.yml by_playbook`.
        members: 펀드의 종목.

    Returns:
        묶음에 적힌 순서의 다리들.

    Raises:
        LegError: 구성원 선언이 없거나 · 자리 배분이 아니거나 · 자리 수가 묶음과 다르거나 ·
            배율이 없거나 · 종목 범위가 없거나 · 어느 다리에도 안 속하는 펀드 종목이 있는 경우.

    Note:
        🔴 **자리 수는 묶음과 같아야 한다.** 멤버 예산은 펀드가 `총자본 ÷ 자리` 로 하나만 낸다 —
        다리마다 자리 수가 다르면 한 종목의 예산이 다리에 따라 달라져야 하는데 세션 예산은 하나다.
    """
    if not wrapper.split_legs:
        return ()
    by_id = {item.playbook_id: item for item in books}
    out: list[FundLeg] = []
    for name in wrapper.bundle:
        book = by_id.get(name)
        if book is None:
            raise LegError(f"{wrapper.playbook_id}.bundle 의 {name!r} 선언이 없다")
        if book.weight_mode != "slots" or book.slots <= 0:
            raise LegError(f"{name} — 다리는 자리 배분(weight_mode: slots · slots)이어야 한다")
        if book.slots != wrapper.slots:
            raise LegError(f"{name}.slots {book.slots} 가 묶음의 자리 수 {wrapper.slots} 와 다르다")
        if book.leverage is None or book.leverage <= 0:
            raise LegError(f"{name} — 다리는 leverage 를 선언해야 한다")
        scope = [str(item) for item in scopes.get(name, ())]
        if not scope:
            raise LegError(f"{name} — 종목 범위(baskets.yml by_playbook.{name})가 없다")
        mine = tuple(symbol for symbol in members if symbol in set(scope))
        if not mine:
            raise LegError(f"{name} — 펀드 종목 중에 이 다리의 종목이 하나도 없다")
        out.append(
            FundLeg(
                playbook=book.playbook_id,
                attribution=book.attribution,
                symbols=mine,
                leverage=book.leverage,
                exposure=book.leverage if book.leg_exposure is None else book.leg_exposure,
                timeframe=book.timeframe.value,
                slots=book.slots,
                halt_after_stops=book.halt_after_stops,
                notional_cap=book.notional_cap,
                notional_fit=book.notional_fit,
                drawdown_brake=book.drawdown_brake,
                breadth_cap=book.breadth_cap,
                halt_dd_at=book.entry_fund_dd_max,
            )
        )
    covered = {symbol for leg in out for symbol in leg.symbols}
    orphans = [symbol for symbol in members if symbol not in covered]
    if orphans:
        raise LegError(f"어느 다리에도 안 속하는 종목이 있다: {', '.join(orphans)}")
    return tuple(out)


def legs_on(legs: Sequence[FundLeg], symbol: str) -> tuple[FundLeg, ...]:
    """그 종목에 실리는 다리들 — 묶음에 적힌 순서."""
    return tuple(leg for leg in legs if symbol in leg.symbols)


def member_playbook(legs: Sequence[FundLeg], symbol: str) -> str:
    """그 종목의 세션에 실을 매매법 이름 — `a+b`(세션 세트 문법).

    Args:
        legs: 펀드의 다리들.
        symbol: 종목.

    Returns:
        그 종목을 가진 다리들의 id 를 `+` 로 이은 것.

    Raises:
        LegError: 그 종목을 가진 다리가 없다.
    """
    mine = legs_on(legs, symbol)
    if not mine:
        raise LegError(f"{symbol} 은 어느 다리의 종목도 아니다")
    return "+".join(leg.playbook for leg in mine)


def member_leverage(legs: Sequence[FundLeg], symbol: str) -> Decimal:
    """그 종목의 세션(거래소) 배율 — 실린 다리들의 최댓값.

    Raises:
        LegError: 그 종목을 가진 다리가 없다.
    """
    mine = legs_on(legs, symbol)
    if not mine:
        raise LegError(f"{symbol} 은 어느 다리의 종목도 아니다")
    return max(leg.leverage for leg in mine)


def leg_gate(
    ports: Mapping[str, object],
    legs: Sequence[FundLeg],
    drawdown: Callable[[], Decimal],
) -> LegGate:
    """다리마다 문을 세워 하나로 묶는다.

    Args:
        ports: 펀드 조정자의 `{종목: 포트}` — 복사하지 않는다(종목을 넣고 빼면 문도 따라간다).
        legs: 펀드의 다리들.
        drawdown: 펀드의 고점 대비 낙폭(0~1) — 브레이크나 낙폭 끄기(`halt_dd_at`)를
            선언한 다리만 쓴다.

    Returns:
        다리별 문.

    Note:
        줄여서 진입의 허용 하한은 단일 문과 같은 규칙(다리 배율 ÷ 4)이다.
    """
    gates: dict[str, SlotGate] = {}
    for leg in legs:
        brake = leg.drawdown_brake
        breadth = leg.breadth_cap
        watch = brake is not None or leg.halt_dd_at is not None
        gates[leg.attribution] = SlotGate(
            ports=LegPorts(ports, leg.attribution, frozenset(leg.symbols)),
            slots=leg.slots,
            halt_after_stops=leg.halt_after_stops,
            notional_cap=leg.notional_cap,
            notional_fit=leg.notional_fit,
            min_grant=leg.exposure / Decimal(4),
            drawdown=drawdown if watch else None,
            brake_at=Decimal(0) if brake is None else brake.at,
            brake_scale=Decimal(1) if brake is None else brake.scale,
            breadth_min=0 if breadth is None else breadth.min,
            breadth_cap=None if breadth is None else breadth.cap,
            halt_dd_at=Decimal(0) if leg.halt_dd_at is None else leg.halt_dd_at,
        )
    return LegGate(gates)
