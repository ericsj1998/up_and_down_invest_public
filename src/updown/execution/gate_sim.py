"""Gate 무기한 선물 **로컬 모의 체결** 어댑터 — 주문을 안 내고 이 프로세스가 체결을 흉내 낸다.

Gate 테스트넷에 없는 계약(예: 지수 무기한)의 주문 흐름을 실계좌 전에 확인하려고 만든다
(T532 D4 "로컬 모의 체결로 먼저"). 체결 규칙:

- 시장가 = 마지막으로 본 값(`observe` 의 종가)에 즉시 · 테이커 요율.
- 지정가 = 놓을 때 이미 넘었으면 즉시 그 값에(테이커) · 아니면 걸어 두고, 본 구간의 저가 · 고가가
  지정가를 `touch` 만큼 넘으면 **지정가에** 전량(메이커 요율).
- 조건부(손절) = 본 구간이 발동가에 닿으면 발동가에 · 몫 크기만큼(reduce-only).
- 증거금 = 격리 · 포지션 명목 ÷ 배율 + 걸린 매수 지정가 명목 ÷ 배율. 가용 = 현금 - 그 둘.

한 계약 · 한 방향(롱) 단방향 포지션만 다룬다 — 이 어댑터를 쓰는 다리가 그것뿐이다.

⛔ **직접 만들지 않는다.** `execution/gateway.sim_adapter` 만 만든다(절대 규칙 #0 · AST 가드).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from updown.common.domain.instrument import Instrument, Side
from updown.common.domain.order import OrderKind, OrderRequest, OrderResult, OrderStatus, OrderType
from updown.common.logging.setup import get_logger

_logger = get_logger("execution.gate_sim")


class SpecSource(Protocol):
    """계약 명세를 내는 조회 어댑터 면."""

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """계약 명세 원문."""
        ...


@dataclass(slots=True)
class _Order:
    oid: str
    text: str
    size: int  # 부호 있음(매수 +)
    price: Decimal | None
    reduce_only: bool
    status: str = "open"  # open | finished
    left: int = 0
    fill_price: Decimal | None = None
    finish_as: str = ""
    maker: bool = False
    created: float = 0.0


@dataclass(slots=True)
class _Stop:
    sid: str
    text: str
    trigger: Decimal
    size: int  # 닫을 계약 수(양수)
    created: float = 0.0


@dataclass(slots=True)
class _Book:
    cash: Decimal
    size: int = 0
    entry: Decimal = Decimal(0)
    leverage: Decimal = Decimal(1)
    last: Decimal | None = None
    orders: dict[str, _Order] = field(default_factory=dict[str, _Order])
    stops: dict[str, _Stop] = field(default_factory=dict[str, _Stop])
    realized: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)


def _text(key: str) -> str:
    """Gate 와 같은 모양의 주문 이름(`t-` · 콜론 → 하이픈)."""
    body = "".join(ch if (ch.isalnum() and ch.isascii()) or ch in "-_." else "-" for ch in key)
    return body if body.startswith("t-") else f"t-{body}"


class GateSimAdapter:
    """한 계약의 로컬 모의 체결.

    Note:
        ⛔ 직접 만들지 않는다 — `execution/gateway.sim_adapter` 만(절대 규칙 #0).
    """

    def __init__(
        self,
        specs: SpecSource,
        cash: Decimal,
        *,
        touch: Decimal = Decimal(0),
        state_path: Path | None = None,
        spec_override: Mapping[str, Any] | None = None,
    ) -> None:
        """모의 계좌를 연다(파일이 있으면 이어 받는다).

        Args:
            specs: 계약 명세 출처(공개 조회 어댑터).
            cash: 시작 현금(USDT).
            touch: 지정가 · 발동가를 넘어야 하는 여유 비율(0 = 닿으면 체결).
            state_path: 상태 파일(여러 날 도는 모의를 다시 띄워도 이어지게).
            spec_override: 명세를 덮어쓸 값(시험 · 호가 단위 고정).
        """
        self._specs = specs
        self._touch = touch
        self._path = state_path
        self._override = dict(spec_override or {})
        self._seen: dict[str, Any] = {}
        self._ids = itertools.count(1)
        self._b = _Book(cash=cash)
        if state_path is not None and state_path.exists():
            self._load(state_path)

    # ── 모의 구동 ──────────────────────────────────────────────────────────────

    def observe(self, high: Decimal, low: Decimal, last: Decimal) -> None:
        """값 구간 하나를 본다 — 걸린 지정가 · 조건부를 체결하고 마지막 값을 갱신한다.

        같은 구간에서 손절이 먼저 닿았다고 본다(보수) · 그다음 매도 지정가 · 매수 지정가.
        """
        b = self._b
        for st in sorted(b.stops.values(), key=lambda s: -s.trigger):
            if b.size > 0 and low <= st.trigger:
                n = min(st.size, b.size)
                self._fill_close(n, st.trigger, maker=False)
                del b.stops[st.sid]
                _logger.info(
                    "gate_sim_stop", payload={"text": st.text, "n": n, "px": str(st.trigger)}
                )
        for o in sorted(b.orders.values(), key=lambda x: x.created):
            if o.status != "open" or o.price is None:
                continue
            if o.size < 0 and high >= o.price * (Decimal(1) + self._touch):
                n = min(o.left, b.size) if o.reduce_only else o.left
                if n > 0:
                    self._fill_close(n, o.price, maker=True)
                self._finish(o, filled=n, px=o.price, maker=True)
            elif o.size > 0 and low <= o.price * (Decimal(1) - self._touch):
                self._fill_open(o.left, o.price, maker=True)
                self._finish(o, filled=o.left, px=o.price, maker=True)
        b.last = last
        self._save()

    # ── 주문 면(Gate 어댑터와 같은 이름) ─────────────────────────────────────────

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """명세 — 공개 조회 값 위에 덮어쓰기."""
        raw = dict(await self._specs.contract_spec(instrument))
        raw.update(self._override)
        self._seen = raw
        return raw

    async def set_leverage(self, instrument: Instrument, leverage: Decimal) -> None:
        """격리 배율."""
        del instrument  # 한 계약만 다룬다 — Gate 면과 서명을 맞추려고 받는다
        if leverage <= 0:
            raise ValueError("배율 0 은 교차 증거금이다 — 모의도 받지 않는다")
        self._b.leverage = leverage
        self._save()

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """주문을 받는다 — 시장가는 즉시, 지정가는 넘었으면 즉시 · 아니면 걸어 둔다."""
        b = self._b
        if b.last is None:
            raise RuntimeError("모의 거래소가 아직 값을 못 봤다 — observe 먼저")
        text = _text(order.idempotency_key)
        if any(o.text == text for o in b.orders.values()):
            raise RuntimeError(f"같은 이름 주문이 이미 있다: {text}")
        n = int(order.quantity)
        signed = n if order.side is Side.BUY else -n
        reduce_only = order.order_kind in (
            OrderKind.TAKE_PROFIT,
            OrderKind.STOP_LOSS,
            OrderKind.CLOSE,
        )
        o = _Order(str(next(self._ids)), text, signed, order.price, reduce_only, left=n)
        o.created = float(len(b.orders))
        b.orders[o.oid] = o
        crosses = order.order_type is OrderType.MARKET or (
            order.price is not None
            and ((signed > 0 and b.last <= order.price) or (signed < 0 and b.last >= order.price))
        )
        if crosses:
            px = b.last
            if signed > 0:
                self._fill_open(n, px, maker=False)
                self._finish(o, filled=n, px=px, maker=False)
            else:
                k = min(n, b.size) if reduce_only else n
                if k > 0:
                    self._fill_close(k, px, maker=False)
                self._finish(o, filled=k, px=px, maker=False)
        self._save()
        return self._result(o)

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        """걸린 주문을 거둔다 — 이미 끝났으면 예외(Gate 와 같다)."""
        o = self._b.orders.get(broker_order_id)
        if o is None or o.status != "open":
            raise RuntimeError(f"ORDER_NOT_FOUND {broker_order_id}")
        o.status = "finished"
        o.finish_as = "cancelled"
        self._save()
        return self._result(o)

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸린 주문."""
        del instrument  # 한 계약만 다룬다 — Gate 면과 서명을 맞추려고 받는다
        return [self._row(o) for o in self._b.orders.values() if o.status == "open"]

    async def order_detail(self, broker_order_id: str) -> dict[str, str] | None:
        """주문 하나(id 또는 멱등키)."""
        o = self._b.orders.get(broker_order_id)
        if o is None:
            text = _text(broker_order_id)
            o = next((x for x in self._b.orders.values() if x.text == text), None)
        return None if o is None else self._row(o)

    async def stops_for(
        self,
        instrument: Instrument,
        trigger: Decimal,
        *,
        long: bool,
        size: int | None = None,
        key: str | None = None,
    ) -> str | None:
        """몫 손절 — 같은 이름 · 값 · 크기면 그대로(None) · 다르면 갈아 끼운다."""
        del instrument  # 한 계약만 다룬다 — Gate 면과 서명을 맞추려고 받는다
        if not long or size is None or not key:
            raise NotImplementedError("모의는 롱 몫 손절(크기 · 이름)만")
        text = _text(key)
        same = [s for s in self._b.stops.values() if s.text == text]
        if any(s.trigger == trigger and s.size == size for s in same):
            return None
        for s in same:
            del self._b.stops[s.sid]
        sid = f"s{next(self._ids)}"
        self._b.stops[sid] = _Stop(sid, text, trigger, size)
        self._save()
        return sid

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸린 조건부."""
        del instrument  # 한 계약만 다룬다 — Gate 면과 서명을 맞추려고 받는다
        return [
            {"id": s.sid, "text": s.text, "trigger_price": str(s.trigger), "size": str(-s.size)}
            for s in self._b.stops.values()
        ]

    async def cancel_stop(self, stop_id: str) -> None:
        """조건부를 거둔다."""
        self._b.stops.pop(stop_id, None)
        self._save()

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        """포지션 — 없으면 빈 사전."""
        del instrument  # 한 계약만 다룬다 — Gate 면과 서명을 맞추려고 받는다
        b = self._b
        if b.size == 0:
            return {}
        mark = b.last or b.entry
        return {
            "size": str(b.size),
            "entry_price": str(b.entry),
            "mark_price": str(mark),
            "leverage": str(b.leverage),
            "margin": str(self._pos_margin()),
        }

    async def margins(self) -> dict[str, str]:
        """계좌 — 가용 = 현금 - 포지션 증거금 - 매수 지정가 증거금."""
        b = self._b
        pos = self._pos_margin()
        orders = sum(
            (
                Decimal(o.left) * (o.price or Decimal(0)) * self._quanto / b.leverage
                for o in b.orders.values()
                if o.status == "open" and o.size > 0
            ),
            Decimal(0),
        )
        unreal = Decimal(b.size) * ((b.last or b.entry) - b.entry) * self._quanto
        return {
            "total": str(b.cash + unreal),
            "available": str(b.cash - pos - orders),
            "position_margin": str(pos),
            "order_margin": str(orders),
        }

    # ── 안쪽 ──────────────────────────────────────────────────────────────────

    @property
    def _quanto(self) -> Decimal:
        got = self._seen.get("quanto_multiplier") or self._override.get("quanto_multiplier")
        if not got:
            raise RuntimeError("모의 거래소가 계약 명세를 아직 안 읽었다 — contract_spec 먼저")
        return Decimal(str(got))

    def _rate(self, maker: bool) -> Decimal:
        key = "maker_fee_rate" if maker else "taker_fee_rate"
        got = self._seen.get(key) or self._override.get(key)
        return (
            Decimal(str(got))
            if got not in (None, "")
            else Decimal("-0.0001" if maker else "0.00075")
        )

    def _pos_margin(self) -> Decimal:
        b = self._b
        return Decimal(b.size) * b.entry * self._quanto / b.leverage

    def _fill_open(self, n: int, px: Decimal, *, maker: bool) -> None:
        b = self._b
        if n <= 0:
            return
        b.entry = (b.entry * Decimal(b.size) + px * Decimal(n)) / Decimal(b.size + n)
        b.size += n
        fee = Decimal(n) * px * self._quanto * self._rate(maker)
        b.cash -= fee
        b.fees += fee

    def _fill_close(self, n: int, px: Decimal, *, maker: bool) -> None:
        b = self._b
        if n <= 0:
            return
        pnl = Decimal(n) * (px - b.entry) * self._quanto
        fee = Decimal(n) * px * self._quanto * self._rate(maker)
        b.cash += pnl - fee
        b.realized += pnl - fee
        b.fees += fee
        b.size -= n
        if b.size == 0:
            b.entry = Decimal(0)

    def _finish(self, o: _Order, *, filled: int, px: Decimal, maker: bool) -> None:
        o.left -= filled
        o.status = "finished"
        o.finish_as = "filled" if o.left == 0 else "ioc"
        o.fill_price = px if filled > 0 else None
        o.maker = maker

    def _row(self, o: _Order) -> dict[str, str]:
        return {
            "id": o.oid,
            "size": str(o.size),
            "left": str(o.left if o.size > 0 else -o.left),
            "price": "" if o.price is None else str(o.price),
            "fill_price": "" if o.fill_price is None else str(o.fill_price),
            "text": o.text,
            "status": o.status,
            "finish_as": o.finish_as,
            "is_reduce_only": str(o.reduce_only).lower(),
            "tkfr": str(self._rate(False)),
            "mkfr": str(self._rate(True)),
        }

    def _result(self, o: _Order) -> OrderResult:
        filled = abs(o.size) - o.left
        if o.status == "open":
            status = OrderStatus.SUBMITTED
        elif o.finish_as == "cancelled":
            status = OrderStatus.CANCELLED
        elif o.left == 0:
            status = OrderStatus.FILLED
        elif filled > 0:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.EXPIRED
        return OrderResult(
            broker_order_id=o.oid,
            idempotency_key=o.text,
            status=status,
            filled_quantity=Decimal(filled),
            average_price=o.fill_price,
            ts=datetime.now(UTC),
            reason=None,
        )

    def snapshot(self) -> dict[str, str]:
        """점검용 요약(현금 · 실현 · 수수료 · 포지션)."""
        b = self._b
        return {
            "cash": str(b.cash),
            "realized": str(b.realized),
            "fees": str(b.fees),
            "size": str(b.size),
            "entry": str(b.entry),
            "last": "" if b.last is None else str(b.last),
        }

    def _save(self) -> None:
        if self._path is None:
            return
        b = self._b
        raw: dict[str, Any] = {
            "cash": str(b.cash),
            "size": b.size,
            "entry": str(b.entry),
            "leverage": str(b.leverage),
            "last": None if b.last is None else str(b.last),
            "realized": str(b.realized),
            "fees": str(b.fees),
            "next_id": next(self._ids),
            "orders": [
                {
                    "oid": o.oid,
                    "text": o.text,
                    "size": o.size,
                    "price": None if o.price is None else str(o.price),
                    "reduce_only": o.reduce_only,
                    "status": o.status,
                    "left": o.left,
                    "fill_price": None if o.fill_price is None else str(o.fill_price),
                    "finish_as": o.finish_as,
                    "maker": o.maker,
                    "created": o.created,
                }
                for o in b.orders.values()
            ],
            "stops": [
                {"sid": s.sid, "text": s.text, "trigger": str(s.trigger), "size": s.size}
                for s in b.stops.values()
            ],
        }
        self._ids = itertools.count(int(raw["next_id"]))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def _load(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        b = _Book(
            cash=Decimal(raw["cash"]),
            size=int(raw["size"]),
            entry=Decimal(raw["entry"]),
            leverage=Decimal(raw["leverage"]),
            last=None if raw["last"] is None else Decimal(raw["last"]),
            realized=Decimal(raw["realized"]),
            fees=Decimal(raw["fees"]),
        )
        for o in raw["orders"]:
            b.orders[o["oid"]] = _Order(
                o["oid"],
                o["text"],
                int(o["size"]),
                None if o["price"] is None else Decimal(o["price"]),
                bool(o["reduce_only"]),
                o["status"],
                int(o["left"]),
                None if o["fill_price"] is None else Decimal(o["fill_price"]),
                o["finish_as"],
                bool(o["maker"]),
                float(o["created"]),
            )
        for s in raw["stops"]:
            b.stops[s["sid"]] = _Stop(s["sid"], s["text"], Decimal(s["trigger"]), int(s["size"]))
        self._b = b
        self._ids = itertools.count(int(raw["next_id"]))
