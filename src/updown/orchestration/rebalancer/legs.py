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
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, cast

from updown.analysis.playbook.types import BreadthCap, DrawdownBrake, EntryLimit, Playbook
from updown.decision.portfolio_rules import drawdown_scale
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
        isolated: 이 다리의 손익을 펀드 낙폭 브레이크에서 뺀다(420차 · `Coordinator.core`).
        entry_limit: 펀드 전체에서 이 다리의 신규 진입 수 상한(452차). None = 없음.
        entry_cap: 한 건 처음 노출 상한(명목 ÷ 자리 예산 · 452차 C75). None = 없음.
        peer_open_max: (짝 다리 귀속 키, 문턱) — 짝 다리 보유가 문턱 이상이면 새로 안 든다
            (512차 N4). None = 없음.
        peer_open_boost: (짝 다리 귀속 키들, 문턱, 배수) — 짝 다리들 보유(이 진입 앞서 연 것)가 문턱
            이상이면 신규 진입 x 배수(T400 · 혼합 3.3). None = 없음.
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
    isolated: bool = False
    entry_limit: EntryLimit | None = None
    entry_cap: Decimal | None = None
    peer_open_max: tuple[str, int] | None = None
    peer_open_boost: tuple[tuple[str, ...], int, Decimal] | None = None

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
            "isolated": self.isolated,
            "entry_limit": (
                None
                if self.entry_limit is None
                else {"count": self.entry_limit.count, "hours": self.entry_limit.hours}
            ),
            "entry_cap": None if self.entry_cap is None else str(self.entry_cap),
            "peer_open_max": (
                None
                if self.peer_open_max is None
                else {"leg": self.peer_open_max[0], "count": self.peer_open_max[1]}
            ),
            "peer_open_boost": (
                None
                if self.peer_open_boost is None
                else {
                    "legs": list(self.peer_open_boost[0]),
                    "min": self.peer_open_boost[1],
                    "mult": str(self.peer_open_boost[2]),
                }
            ),
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
        raw_limit = data.get("entry_limit")  # 1.23.0 앞 저장본엔 없다 — 없으면 끔
        raw_entry_cap = data.get("entry_cap")
        raw_peer = data.get("peer_open_max")  # T320 앞 저장본엔 없다 — 없으면 끔
        raw_boost = data.get("peer_open_boost")  # 1.36.0 앞 저장본엔 없다 — 없으면 끔
        boost: tuple[tuple[str, ...], int, Decimal] | None = None
        if isinstance(raw_boost, Mapping):
            body = cast("Mapping[str, Any]", raw_boost)
            boost = (
                tuple(str(item) for item in cast("Sequence[object]", body["legs"])),
                int(body["min"]),
                Decimal(str(body["mult"])),
            )
        peer = None
        if isinstance(raw_peer, Mapping):
            body = cast("Mapping[str, Any]", raw_peer)
            peer = (str(body["leg"]), int(body["count"]))
        limit = None
        if isinstance(raw_limit, Mapping):
            body = cast("Mapping[str, Any]", raw_limit)
            limit = EntryLimit(count=int(body["count"]), hours=int(body["hours"]))
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
            isolated=bool(data.get("isolated", False)),  # 1.20 앞 저장본엔 없다 — 없으면 섞음
            entry_limit=limit,
            entry_cap=None if raw_entry_cap in (None, "") else Decimal(str(raw_entry_cap)),
            peer_open_max=peer,
            peer_open_boost=boost,
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
        peer: tuple[str, int] | None = None
        if book.entry_peer_open_max is not None:
            cap = book.entry_peer_open_max
            peer_book = by_id.get(cap.leg)
            if peer_book is None or cap.leg not in wrapper.bundle or cap.leg == name:
                raise LegError(
                    f"{name}.entry_peer_open_max.leg {cap.leg!r} — 같은 묶음의 다른 구성원이 아니다"
                )
            peer = (peer_book.attribution, cap.count)
        boost: tuple[tuple[str, ...], int, Decimal] | None = None
        if book.entry_peer_open_boost is not None:
            spec = book.entry_peer_open_boost
            attrs: list[str] = []
            for peer_name in spec.legs:
                boost_book = by_id.get(peer_name)
                if boost_book is None or peer_name not in wrapper.bundle or peer_name == name:
                    raise LegError(
                        f"{name}.entry_peer_open_boost.legs {peer_name!r} — "
                        "같은 묶음의 다른 구성원이 아니다"
                    )
                attrs.append(boost_book.attribution)
            boost = (tuple(attrs), spec.min, spec.mult)
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
                isolated=book.drawdown_isolated,
                entry_limit=book.entry_limit,
                entry_cap=book.entry_exposure_cap,
                peer_open_max=peer,
                peer_open_boost=boost,
            )
        )
    covered = {symbol for leg in out for symbol in leg.symbols}
    orphans = [symbol for symbol in members if symbol not in covered]
    if orphans:
        raise LegError(f"어느 다리에도 안 속하는 종목이 있다: {', '.join(orphans)}")
    return tuple(out)


def refresh_legs(
    stored: Sequence[FundLeg], declared: Sequence[FundLeg]
) -> tuple[tuple[FundLeg, ...], tuple[str, ...]]:
    """돌던 펀드의 다리 **계좌 층 값**을 선언에서 다시 읽는다 (411차 · 다리 개정 번호가 오를 때만).

    저장본이 선언을 이기는 원칙(T286 · T291)의 **명시적 예외**다 — 묶음 매매법이 `legs_revision` 을
    올렸을 때만 불린다. 종목(`symbols`)과 귀속 키(`attribution`)는 저장본 그대로다: 종목은 사람이
    바구니에서 고친 것이고, 귀속 키가 바뀌면 펀드 문이 모든 진입을 막는다.

    Args:
        stored: 저장본 다리들.
        declared: 지금 선언으로 만든 다리들.

    Returns:
        (새 다리들, 바뀐 다리 설명). 선언에 없는 귀속 키의 다리는 그대로 둔다(설명에 적는다).
    """
    by = {leg.attribution: leg for leg in declared}
    out: list[FundLeg] = []
    notes: list[str] = []
    for leg in stored:
        fresh = by.get(leg.attribution)
        if fresh is None:
            out.append(leg)
            notes.append(f"{leg.attribution}: 선언에 같은 귀속 키가 없어 저장본 그대로")
            continue
        made = replace(fresh, symbols=leg.symbols)
        if made != leg:
            notes.append(f"{leg.attribution}: {leg.to_dict()} → {made.to_dict()}")
        out.append(made)
    return tuple(out), tuple(notes)


def isolated_attributions(legs: Sequence[FundLeg]) -> frozenset[str]:
    """브레이크에서 뺀 다리들의 귀속 키 (420차)."""
    return frozenset(leg.attribution for leg in legs if leg.isolated)


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
    by_attr = {leg.attribution: leg for leg in legs}
    for leg in legs:
        brake = leg.drawdown_brake
        breadth = leg.breadth_cap
        watch = brake is not None or leg.halt_dd_at is not None
        peer_ports: LegPorts | None = None
        peer_max = 0
        if leg.peer_open_max is not None:
            peer_attr, peer_max = leg.peer_open_max
            peer_leg = by_attr.get(peer_attr)
            # 짝 다리가 저장본에 없으면(묶음을 바꿔 되살림) 모르는 채 세지 않는다 — 빈 범위로 센다.
            scope: frozenset[str] = frozenset() if peer_leg is None else frozenset(peer_leg.symbols)
            peer_ports = LegPorts(ports, peer_attr, scope)
        boost_ports: tuple[LegPorts, ...] = ()
        boost_min, boost_mult = 0, Decimal(1)
        if leg.peer_open_boost is not None:
            boost_attrs, boost_min, boost_mult = leg.peer_open_boost
            # 짝 다리가 저장본에 없으면 빈 범위 — 보유 수는 범위와 무관하게 그 다리 기록을 센다.
            boost_ports = tuple(
                LegPorts(
                    ports,
                    attr,
                    frozenset() if by_attr.get(attr) is None else frozenset(by_attr[attr].symbols),
                )
                for attr in boost_attrs
            )
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
            entry_limit=0 if leg.entry_limit is None else leg.entry_limit.count,
            entry_window_hours=24 if leg.entry_limit is None else leg.entry_limit.hours,
            entry_cap=leg.entry_cap,
            peer_ports=peer_ports,
            peer_open_max=peer_max,
            boost_ports=boost_ports,
            boost_open_min=boost_min,
            boost_mult=boost_mult,
        )
    return LegGate(gates)


def brake_view(
    legs: Sequence[FundLeg],
    drawdown: Decimal,
    *,
    fund_brake: DrawdownBrake | None = None,
    own_pnl: bool,
    names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """지금 **브레이크가 걸려 있나** — 화면 · 프로브가 읽는 한 덩어리 (사용자 2026-10-09).

    Args:
        legs: 펀드의 다리들(저장본). 비어 있으면 `fund_brake` 하나로 본다.
        drawdown: 브레이크가 보는 낙폭(0~1 · `Coordinator.brake_drawdown`).
        fund_brake: 다리 없는 펀드의 브레이크 선언. 다리가 있으면 안 읽는다.
        own_pnl: 참이면 낙폭 출처가 **매매법 실현만**(브레이크 원장 · T387)이다 — 같은
            계좌의 수동 매매 · 입출금은 안 들어간다. 거짓이면 펀드 원장(계좌 총액 앵커).
        names: 다리 귀속 키 → 짧은 이름. 없으면 매매법 id.

    Returns:
        `{source, drawdown_pct, engaged, scale, recover_pct, legs}`.
        `scale` = 지금 신규 진입에 곱해지는 가장 작은 배수(안 걸리면 "1") ·
        `recover_pct` = 고점으로 돌아가는 데 필요한 실현 수익률(%) ·
        `legs` = 다리마다 `{playbook, name, at, scale, engaged, isolated}`
        (브레이크 없는 다리는 `at` None).

    Note:
        사용자 2026-10-09 — 수동 매매로 100 달러쯤 잃고 "브레이크가 걸려 있나" 물었다.
        그날 펀드 원장 낙폭 41.6% · 브레이크 원장 0% 였는데 화면엔 계좌 낙폭 하나뿐이라
        둘이 같은 것으로 읽혔다. 걸림 판정은 다리 문과 **같은 자**(`drawdown_scale` ·
        엄격 부등호)다 — 화면이 따로 세면 문과 화면이 갈린다. 판단은 없다(읽어 적을 뿐).
    """
    dd = max(drawdown, Decimal(0))
    rows: list[dict[str, Any]] = []
    scales: list[Decimal] = []

    def row(key: str, name: str, brake: DrawdownBrake | None, isolated: bool) -> None:
        now = Decimal(1) if brake is None else drawdown_scale(dd, brake.at, brake.scale)
        if brake is not None and not isolated:
            scales.append(now)
        rows.append(
            {
                "playbook": key,
                "name": name,
                "at": None if brake is None else str(brake.at),
                "scale": None if brake is None else str(brake.scale),
                "engaged": now != 1 and not isolated,
                "isolated": isolated,
            }
        )

    if legs:
        for leg in legs:
            label = (
                (names or {}).get(leg.attribution)
                or (names or {}).get(leg.playbook)
                or leg.playbook
            )
            row(leg.attribution, label, leg.drawdown_brake, leg.isolated)
    elif fund_brake is not None:
        row("", "펀드", fund_brake, False)
    scale = min(scales) if scales else Decimal(1)
    recover = (
        None if dd <= 0 or dd >= 1 else (Decimal(1) / (Decimal(1) - dd) - Decimal(1)) * Decimal(100)
    )
    return {
        "source": "own_pnl" if own_pnl else "fund",
        "drawdown_pct": str(dd * Decimal(100)),
        "engaged": scale != 1,
        "scale": str(scale),
        "recover_pct": None if recover is None else str(recover),
        "legs": rows,
    }
