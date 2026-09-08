"""대기 중 진입 계획의 **영속화와 복원** (T218 · 2026-09-05).

세션의 대기 상태(`_waiting` · `_tickets` · `_waiting_until`)는 메모리에만 있었다. 그래서 판이 다시
뜨면(배포 · 재시작) 거래소에 걸린 진입 지정가는 주인이 없어 보였고 `sweep_zombie_entries` 가
거뒀다 — 손익은 없지만 **그 봉 안의 체결 기회**를 잃는다. 실계좌 첫날 배포 한 번에 실제로
그렇게 됐다.

여기서는 세 가지를 한다:

1. `snapshot()` — 세션·우편함에서 대기 계획을 **읽어** 직렬화 가능한 값으로 만든다
   (러너가 걸음마다 부른다).
2. `to_json()` / `from_json()` — `wf_runs.meta_json["pending_entry"]` 에 넣고 꺼낸다.
3. `plan_restore()` — 되살릴 때 거래소 열린 주문과 대조해 **표마다** 무엇을 할지 정한다(순수 함수).

⚠️ 판정은 없다. 어느 표를 이어받고 어느 표를 버릴지는 **거래소가 말한 사실**(주문이 아직 있나 ·
채워졌나)로만 가른다 — 여기서 추측을 섞으면 유령 포지션이 된다 (2026-08-19 사고 ③ 과 같은 병).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, cast

from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    HalfBy,
    Outcome,
    TradeRecord,
    evidence_from_rows,
    evidence_rows,
)

META_KEY = "pending_entry"
"""`wf_runs.meta_json` 안의 자리."""


@dataclass(frozen=True, slots=True)
class PendingTicket:
    """사다리 다리 하나 — 표 이름과 거래소 주문 id (아직 안 보냈으면 None)."""

    ticket: str
    order_id: str | None
    price: Decimal
    ratio: Decimal
    long: bool


@dataclass(frozen=True, slots=True)
class PendingEntry:
    """세션이 기다리는 매매 하나와 그 다리들."""

    record: TradeRecord
    tickets: tuple[PendingTicket, ...]
    waiting_until: datetime | None


class _SessionView(Protocol):
    def pending_entry(self) -> tuple[TradeRecord, tuple[str, ...], datetime | None] | None:
        """대기 중인 계획 · 표 이름들 · 대기 만료 시각.

        Returns:
            대기가 없으면 None.
        """
        ...


class _FillerView(Protocol):
    def mine(self, ticket: str) -> str | None:
        """표에 붙은 멱등키.

        Args:
            ticket: 표 이름.

        Returns:
            멱등키. 모르는 표면 None.
        """
        ...

    def ratio_of(self, ticket: str) -> Decimal | None:
        """표의 비중.

        Args:
            ticket: 표 이름.

        Returns:
            비중. 모르는 표면 None.
        """
        ...

    def pending_want(self, ticket: str) -> tuple[Decimal, Decimal, bool] | None:
        """아직 안 보낸 표의 (가격, 비중, 롱).

        Args:
            ticket: 표 이름.

        Returns:
            모르는 표면 None.
        """
        ...


def snapshot(session: _SessionView, filler: _FillerView | None) -> PendingEntry | None:
    """지금 대기 중인 계획을 읽는다. 없으면 None.

    Args:
        session: `pending_entry()` 를 주는 세션.
        filler: 우편함 — 표의 거래소 주문 id·비중·(아직 안 보낸 것의) 가격을 안다.

    Returns:
        대기 계획. 세션이 기다리는 것이 없으면 None.

    Note:
        아직 안 보낸 표(`order_id=None`)도 담는다 — 되살릴 때 다시 부탁하면 된다. 보낸 것도 채워진
        것도 아닌, 우편함이 모르는 표는 **뺀다**(거절된 표) — 세션이 다음 걸음에 계획을 접을 것이다.
    """
    found = session.pending_entry()
    if found is None or filler is None:
        return None
    record, tickets, until = found
    legs: list[PendingTicket] = []
    for ticket in tickets:
        order_id = filler.mine(ticket)
        if order_id is not None:
            ratio = filler.ratio_of(ticket)
            want = filler.pending_want(ticket)
            price = want[0] if want is not None else Decimal(0)
            long = want[2] if want is not None else record.direction is Direction.LONG
            legs.append(PendingTicket(ticket, order_id, price, ratio or Decimal(0), long))
            continue
        want = filler.pending_want(ticket)
        if want is not None:
            price, ratio, long = want
            legs.append(PendingTicket(ticket, None, price, ratio, long))
    if not legs:
        return None
    return PendingEntry(record=record, tickets=tuple(legs), waiting_until=until)


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_dt(value: object) -> datetime | None:
    return None if value in (None, "") else datetime.fromisoformat(str(value))


def _dec(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def to_json(pending: PendingEntry) -> dict[str, Any]:
    """JSON 으로 — Decimal 은 문자열, 시각은 ISO, 열거형은 값.

    Args:
        pending: 대기 진입.

    Returns:
        `from_json` 이 되읽는 dict.
    """
    r = pending.record
    return {
        "record": {
            "trade_id": r.trade_id,
            "playbook": r.playbook,
            "actor": r.actor.value,
            "direction": r.direction.value,
            "outcome": r.outcome.value,
            "placed_at": _dt(r.placed_at),
            "opened_at": _dt(r.opened_at),
            "closed_at": _dt(r.closed_at),
            "half_at": _dt(r.half_at),
            "half_by": None if r.half_by is None else r.half_by.value,
            "half_price": None if r.half_price is None else str(r.half_price),
            "entry": str(r.entry),
            "exit_price": None if r.exit_price is None else str(r.exit_price),
            "planned_stop": str(r.planned_stop),
            "planned_first": str(r.planned_first),
            "planned_target": str(r.planned_target),
            "cost_pct": str(r.cost_pct),
            "leverage": str(r.leverage),
            "hold_level": None if r.hold_level is None else str(r.hold_level),
            "confirmed": r.confirmed,
            "entry_fills": [[str(p), str(q)] for p, q in r.entry_fills],
            "note": r.note,
            "evidence": evidence_rows(r.evidence),
        },
        "tickets": [
            {
                "ticket": t.ticket,
                "order_id": t.order_id,
                "price": str(t.price),
                "ratio": str(t.ratio),
                "long": t.long,
            }
            for t in pending.tickets
        ],
        "waiting_until": _dt(pending.waiting_until),
    }


def from_json(raw: Mapping[str, Any]) -> PendingEntry:
    """`to_json` 의 역.

    Args:
        raw: `to_json` 이 만든 dict.

    Returns:
        대기 진입.

    Raises:
        ValueError: `placed_at` 이 없다. 모르는 열거형 값도 여기서 터지는 것이 맞다 (규칙 #8).
    """
    r = raw["record"]
    placed_at = _parse_dt(r.get("placed_at"))
    if placed_at is None:
        raise ValueError("pending_entry.record.placed_at 이 없다")
    record = TradeRecord(
        trade_id=str(r["trade_id"]),
        playbook=str(r["playbook"]),
        actor=Actor(r["actor"]),
        placed_at=placed_at,
        entry=Decimal(str(r["entry"])),
        opened_at=_parse_dt(r.get("opened_at")),
        planned_stop=Decimal(str(r.get("planned_stop", "0"))),
        planned_target=Decimal(str(r.get("planned_target", "0"))),
        planned_first=Decimal(str(r.get("planned_first", "0"))),
        direction=Direction(r["direction"]),
        outcome=Outcome(r["outcome"]),
        closed_at=_parse_dt(r.get("closed_at")),
        exit_price=_dec(r.get("exit_price")),
        half_at=_parse_dt(r.get("half_at")),
        cost_pct=Decimal(str(r.get("cost_pct", "0"))),
        leverage=Decimal(str(r.get("leverage", "1"))),
        hold_level=_dec(r.get("hold_level")),
        confirmed=r.get("confirmed"),
        entry_fills=tuple(
            (Decimal(str(p)), Decimal(str(q)))
            for p, q in cast("list[list[str]]", r.get("entry_fills") or [])
        ),
        half_by=None if r.get("half_by") in (None, "") else HalfBy(r["half_by"]),
        half_price=_dec(r.get("half_price")),
        evidence=evidence_from_rows(r.get("evidence") or []),
        note=r.get("note"),
    )
    tickets = tuple(
        PendingTicket(
            ticket=str(t["ticket"]),
            order_id=None if t.get("order_id") in (None, "") else str(t["order_id"]),
            price=Decimal(str(t["price"])),
            ratio=Decimal(str(t["ratio"])),
            long=bool(t.get("long", True)),
        )
        for t in raw.get("tickets", [])
    )
    return PendingEntry(
        record=record, tickets=tickets, waiting_until=_parse_dt(raw.get("waiting_until"))
    )


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """표마다 무엇을 할지 — 거래소 사실로만 가른 결과."""

    inherit: tuple[PendingTicket, ...]
    """거래소에 아직 걸려 있다 → 우편함에 그대로 심는다."""

    place: tuple[PendingTicket, ...]
    """아직 보낸 적이 없다 → 다시 부탁한다."""

    filled: tuple[tuple[PendingTicket, Decimal], ...]
    """그 사이 채워졌다 → 우편함에 체결로 넣는다 (세션이 다음 걸음에 원장에 적는다)."""

    dropped: tuple[PendingTicket, ...]
    """거래소에 없고 체결도 아니다(취소·만료) → 버린다."""

    @property
    def alive(self) -> bool:
        """되살릴 것이 하나라도 있나."""
        return bool(self.inherit or self.place or self.filled)


def plan_restore(
    pending: PendingEntry,
    open_rows: Iterable[Mapping[str, str]],
    filled_price: Callable[[str], Decimal | None],
) -> RestorePlan:
    """되살릴 때 표마다 할 일을 정한다.

    Args:
        pending: 저장돼 있던 대기 계획.
        open_rows: 거래소가 지금 말하는 열린 주문들 (`id` 키).
        filled_price: 주문 id → 채워진 가격. 모르면 None.

    Returns:
        표별 결정.
    """
    open_ids = {str(row.get("id", "")) for row in open_rows}
    inherit: list[PendingTicket] = []
    place: list[PendingTicket] = []
    filled: list[tuple[PendingTicket, Decimal]] = []
    dropped: list[PendingTicket] = []
    for t in pending.tickets:
        if t.order_id is None:
            place.append(t)
        elif t.order_id in open_ids:
            inherit.append(t)
        else:
            price = filled_price(t.order_id)
            if price is not None:
                filled.append((t, price))
            else:
                dropped.append(t)
    return RestorePlan(tuple(inherit), tuple(place), tuple(filled), tuple(dropped))
