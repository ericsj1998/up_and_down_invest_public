"""스냅샷 읽기 — `pull_live_review.sh` 가 받아 둔 폴더를 자료형으로 (T444)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

TRADE_COLUMNS = (
    "run_id",
    "symbol",
    "run_playbook_id",
    "trade_id",
    "playbook",
    "direction",
    "outcome",
    "actor",
    "placed_at",
    "opened_at",
    "closed_at",
    "entry",
    "exit_price",
    "planned_stop",
    "planned_target",
    "leverage",
    "filled_leverage",
    "margin_used",
    "contracts",
    "funding_paid",
    "fee_actual",
    "realized_adjust",
    "half_price",
    "half_at",
    "evidence_json",
)
RUN_COLUMNS = (
    "id",
    "key",
    "symbol",
    "playbook_id",
    "playbook",
    "leverage",
    "margin_budget",
    "opened_at",
    "closed_at",
    "closed_reason",
    "meta_json",
)
ORDER_COLUMNS = (
    "run_id",
    "trade_id",
    "role",
    "status",
    "price",
    "contracts",
    "created_at",
    "fill_price",
    "finish_as",
    "text",
)


def dec(v: object) -> Decimal | None:
    """문자열 → Decimal. 빈 값 · 못 읽는 값은 None (0 으로 꾸미지 않는다)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def when(v: object) -> datetime | None:
    """ISO 문자열(UTC) → aware datetime. 빈 값은 None."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        out = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Trade:
    """실계좌 매매 한 줄 — 원장(`wf_trades`)의 사실만."""

    run_id: str
    symbol: str
    run_playbook_id: str
    trade_id: str
    playbook: str
    direction: str
    outcome: str
    actor: str
    placed_at: datetime | None
    opened_at: datetime | None
    closed_at: datetime | None
    entry: Decimal | None
    exit_price: Decimal | None
    planned_stop: Decimal | None
    planned_target: Decimal | None
    leverage: Decimal | None
    filled_leverage: Decimal | None
    margin_used: Decimal | None
    contracts: int | None
    funding_paid: Decimal | None
    fee_actual: Decimal | None
    realized_adjust: Decimal | None
    half_price: Decimal | None
    half_at: datetime | None
    evidence: dict[str, Any] = field(default_factory=dict[str, Any])

    @property
    def leg(self) -> str:
        """다리 이름 = 귀속 키의 `@` 앞 (`private_strategy`)."""
        return self.playbook.split("@", 1)[0]

    @property
    def is_long(self) -> bool:
        """롱인가 — 원장 방향 문자열(LONG · L · BUY)."""
        return self.direction.strip().upper() in ("LONG", "L", "BUY", "롱", "매수")

    @property
    def closed(self) -> bool:
        """닫혔나 — 청산 시각과 청산가가 둘 다 있다."""
        return self.closed_at is not None and self.exit_price is not None

    @property
    def stop_distance(self) -> Decimal | None:
        """1R = |진입 - 계획 손절| ÷ 진입 (비율). 없으면 None."""
        if self.entry is None or self.planned_stop is None or self.entry == 0:
            return None
        d = abs(self.entry - self.planned_stop) / self.entry
        return d if d > 0 else None

    def r_of(self, price: Decimal) -> Decimal | None:
        """가격 하나를 R 로 — 방향을 맞춘다(롱은 위가 +, 숏은 아래가 +)."""
        d = self.stop_distance
        if d is None or self.entry is None:
            return None
        move = (price - self.entry) / self.entry
        return (move if self.is_long else -move) / d


@dataclass(frozen=True, slots=True)
class Run:
    """실계좌 판 한 줄 — 깔때기(`meta_json["funnel"]`)가 문 통과 · 막힘 수를 싣는다."""

    id: str
    key: str
    symbol: str
    playbook_id: str
    playbook: str
    leverage: Decimal | None
    margin_budget: Decimal | None
    opened_at: datetime | None
    closed_at: datetime | None
    closed_reason: str
    meta: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class Order:
    """실계좌 주문 한 줄(`wf_orders`) — 역할 · 상태 · 가격 · 체결가."""

    run_id: str
    trade_id: str
    role: str
    status: str
    price: Decimal | None
    contracts: str
    created_at: datetime | None
    fill_price: Decimal | None
    finish_as: str
    text: str


@dataclass(slots=True)
class Snapshot:
    """끌어온 한 벌."""

    path: Path
    taken_at: datetime | None
    runs: list[Run]
    trades: list[Trade]
    orders: list[Order]
    fund: dict[str, Any]
    events: list[dict[str, Any]]
    exchange: dict[str, Any]


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    out: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < len(columns):
            parts += [""] * (len(columns) - len(parts))
        out.append(dict(zip(columns, parts, strict=False)))
    return out


def _json(text: str) -> dict[str, Any]:
    try:
        got: object = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", got) if isinstance(got, dict) else {}


def load_snapshot(path: Path) -> Snapshot:
    """폴더를 읽는다. 없는 파일은 빈 값 — 어느 재료가 없었는지는 보고서가 적는다.

    Args:
        path: 스냅샷 폴더(`logs/live_review/snapshots/<시각>` 또는 `latest`).

    Returns:
        스냅샷.
    """
    path = path.resolve()
    taken: datetime | None = None
    meta = path / "meta.txt"
    if meta.exists():
        for tok in meta.read_text(encoding="utf-8").split():
            if tok.startswith("at="):
                taken = when(tok[3:])
    trades = [
        Trade(
            run_id=r["run_id"],
            symbol=r["symbol"],
            run_playbook_id=r["run_playbook_id"],
            trade_id=r["trade_id"],
            playbook=r["playbook"],
            direction=r["direction"],
            outcome=r["outcome"],
            actor=r["actor"],
            placed_at=when(r["placed_at"]),
            opened_at=when(r["opened_at"]),
            closed_at=when(r["closed_at"]),
            entry=dec(r["entry"]),
            exit_price=dec(r["exit_price"]),
            planned_stop=dec(r["planned_stop"]),
            planned_target=dec(r["planned_target"]),
            leverage=dec(r["leverage"]),
            filled_leverage=dec(r["filled_leverage"]),
            margin_used=dec(r["margin_used"]),
            contracts=int(r["contracts"]) if r["contracts"].strip().lstrip("-").isdigit() else None,
            funding_paid=dec(r["funding_paid"]),
            fee_actual=dec(r["fee_actual"]),
            realized_adjust=dec(r["realized_adjust"]),
            half_price=dec(r["half_price"]),
            half_at=when(r["half_at"]),
            evidence=_json(r["evidence_json"]),
        )
        for r in _rows(path / "trades.tsv", TRADE_COLUMNS)
    ]
    runs = [
        Run(
            id=r["id"],
            key=r["key"],
            symbol=r["symbol"],
            playbook_id=r["playbook_id"],
            playbook=r["playbook"],
            leverage=dec(r["leverage"]),
            margin_budget=dec(r["margin_budget"]),
            opened_at=when(r["opened_at"]),
            closed_at=when(r["closed_at"]),
            closed_reason=r["closed_reason"],
            meta=_json(r["meta_json"]),
        )
        for r in _rows(path / "runs.tsv", RUN_COLUMNS)
    ]
    orders = [
        Order(
            run_id=r["run_id"],
            trade_id=r["trade_id"],
            role=r["role"],
            status=r["status"],
            price=dec(r["price"]),
            contracts=r["contracts"],
            created_at=when(r["created_at"]),
            fill_price=dec(r["fill_price"]),
            finish_as=r["finish_as"],
            text=r["text"],
        )
        for r in _rows(path / "orders.tsv", ORDER_COLUMNS)
    ]
    trades = dedupe_trades(trades)
    fund_path = path / "fund.json"
    fund = _json(fund_path.read_text(encoding="utf-8")) if fund_path.exists() else {}
    events: list[dict[str, Any]] = []
    ev_path = path / "events.jsonl"
    if ev_path.exists():
        for line in ev_path.read_text(encoding="utf-8", errors="replace").splitlines():
            got = _json(line)
            if got:
                events.append(got)
    ex_path = path / "exchange.json"
    exchange = _json(ex_path.read_text(encoding="utf-8")) if ex_path.exists() else {}
    return Snapshot(
        path=path,
        taken_at=taken,
        runs=runs,
        trades=trades,
        orders=orders,
        fund=fund,
        events=events,
        exchange=exchange,
    )


def dedupe_trades(trades: list[Trade]) -> list[Trade]:
    """같은 매매 id 가 여러 판에 있으면 하나만 — 전환 때 옛 판 줄은 `취소`(이관) · 새 판이 잇는다.

    Args:
        trades: 읽은 매매들.

    Returns:
        매매 id 마다 한 줄 — `취소` 아닌 줄 우선 · 그다음 청산 시각이 늦은 줄. 순서는 입력 순.
    """
    best: dict[str, Trade] = {}
    order: list[str] = []
    for t in trades:
        cur = best.get(t.trade_id)
        if cur is None:
            best[t.trade_id] = t
            order.append(t.trade_id)
            continue
        floor = datetime.min.replace(tzinfo=UTC)
        cur_cancel = cur.outcome == "취소"
        new_cancel = t.outcome == "취소"
        later = (t.closed_at or t.placed_at or floor) > (cur.closed_at or cur.placed_at or floor)
        if (cur_cancel and not new_cancel) or (cur_cancel == new_cancel and later):
            best[t.trade_id] = t
    return [best[k] for k in order]
