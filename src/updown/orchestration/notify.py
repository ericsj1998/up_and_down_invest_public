"""매매 알림 거리 — 판 원장을 앞 모습과 견줘 **새로 생긴 일**만 뽑는다 (순수 · 2026-09-27).

> *"진입, 익절, 손절, 청산, 경보 등은 컴퓨터, 핸드폰 등 알림으로 띄워주면 좋겠어."*

매매 경로(러너 · 세션)는 건드리지 않는다. 보내는 쪽(`apps/api/notify.py`)이 몇 초마다
판마다 원장 · 실현 금액 · 감사 결과를 넘기면 여기서 앞 모습(`RunState`)과 견줘
알림(`Note`)을 만든다.

- **처음 본 판은 알리지 않는다** — 배포 · 재시작 직후 이미 있던 포지션 · 경보가 한꺼번에
  쏟아지지 않게 모습만 적는다. 재시작 틈에 생긴 일은 놓칠 수 있다(화면 · 리포트가 보여 준다).
- 청산 금액 = 그 판 실현 금액(`Ledger.realized_cash`)의 변화 — 한 번에 한 건이 닫혔을
  때만 붙인다(두 건이면 금액을 가를 수 없다).
- 경보 = 감사 결과 중 `error` 급 · 같은 코드는 6시간에 한 번 · 사라졌다 다시 나오면 다시
  알린다. 기동 잡음(`awaiting_fund`)은 뺀다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from updown.orchestration.walkforward.ledger import Outcome, TradeRecord

ALERT_EVERY = timedelta(hours=6)
"""같은 경보를 다시 알리는 간격."""

QUIET_CODES = frozenset({"awaiting_fund"})
"""알리지 않는 감사 코드 — 배포 · 재시작 직후 1 ~ 2분은 늘 뜨는 정상 잡음이다."""

_LIVE = frozenset({Outcome.PENDING, Outcome.OPEN})


@dataclass(frozen=True, slots=True)
class Note:
    """알림 한 건.

    Attributes:
        kind: `entry` · `close` · `add` · `alert`.
        title: 알림 제목(한 줄).
        body: 본문.
        tag: 같은 tag 의 알림은 기기에서 앞 것을 덮는다 — 매매 하나에 알림 하나.
        good: 청산이 이익이면 참 · 손실이면 거짓 · 그 밖은 None.
    """

    kind: str
    title: str
    body: str
    tag: str
    good: bool | None = None


@dataclass(frozen=True, slots=True)
class Seen:
    """매매 하나의 앞 모습."""

    outcome: Outcome
    adds: int


@dataclass(slots=True)
class RunState:
    """판 하나의 앞 모습 — 다음 비교의 기준."""

    trades: dict[str, Seen] = field(default_factory=dict[str, Seen])
    realized: Decimal = Decimal(0)
    alerts: dict[str, datetime] = field(default_factory=dict[str, datetime])


def _price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def _side(record: TradeRecord) -> str:
    return "숏" if record.direction.value.lower() in {"short", "숏"} else "롱"


def _entry_note(run: str, name: str, record: TradeRecord, label: str) -> Note:
    return Note(
        kind="entry",
        title=f"진입 · {name} {_side(record)}",
        body=f"{label} · 진입 {_price(record.entry)} · 손절 {_price(record.planned_stop)}",
        tag=f"{run}:{record.trade_id}",
    )


def _close_note(
    run: str, name: str, record: TradeRecord, label: str, amount: Decimal | None
) -> Note:
    gain = record.gain_pct
    good = None if gain is None else gain > 0
    mark = "✅" if good else "🔴" if good is False else ""
    pct = "—" if gain is None else f"{gain:+.2f}%"
    money = "" if amount is None else f" · {amount:+,.2f} USDT"
    return Note(
        kind="close",
        title=f"{mark} {record.outcome.value} · {name} {_side(record)}".strip(),
        body=f"{pct}{money} · {label} · {_price(record.entry)} → {_price(record.exit_price)}",
        tag=f"{run}:{record.trade_id}",
        good=good,
    )


def diff_run(
    run: str,
    name: str,
    records: Sequence[TradeRecord],
    realized: Decimal,
    findings: Sequence[Mapping[str, str]],
    state: RunState | None,
    now: datetime,
    *,
    label_of: Callable[[str], str],
) -> tuple[list[Note], RunState]:
    """판 하나의 새 일 → 알림들 · 새 앞 모습 (순수).

    Args:
        run: 판 id(알림 tag).
        name: 종목 짧은 이름(`BTC`).
        records: 원장 매매 기록.
        realized: 원장 실현 금액(`Ledger.realized_cash`).
        findings: 감사 결과(`LiveRunner.findings` · `{code, level, detail}`).
        state: 앞 모습. None 이면 **처음 본 판** — 알리지 않고 모습만 적는다.
        now: 지금(aware) — 경보 간격을 잰다.
        label_of: 귀속 키(`playbook@version`) → 짧은 매매법 이름.

    Returns:
        (알림들, 새 앞 모습).
    """
    fresh = RunState(
        trades={r.trade_id: Seen(r.outcome, r.add_contracts) for r in records},
        realized=realized,
        alerts=dict(state.alerts) if state is not None else {},
    )
    errors = {
        str(f.get("code", "")): str(f.get("detail", ""))
        for f in findings
        if f.get("level") == "error" and f.get("code") not in QUIET_CODES
    }
    if state is None:
        fresh.alerts = dict.fromkeys(errors, now)
        return [], fresh

    notes: list[Note] = []
    closes: list[TradeRecord] = []
    for record in records:
        label = label_of(record.playbook)
        prev = state.trades.get(record.trade_id)
        now_live = record.outcome in _LIVE
        ended = not now_live and record.outcome is not Outcome.CANCELLED
        if prev is None:
            if record.outcome is Outcome.OPEN:
                notes.append(_entry_note(run, name, record, label))
            elif ended:
                closes.append(record)
            continue
        if prev.outcome is Outcome.PENDING and record.outcome is Outcome.OPEN:
            notes.append(_entry_note(run, name, record, label))
        if prev.outcome in _LIVE and ended:
            closes.append(record)
        if record.add_contracts > prev.adds and record.outcome is Outcome.OPEN:
            notes.append(
                Note(
                    kind="add",
                    title=f"불타기 · {name} {_side(record)}",
                    body=f"{label} · {record.add_contracts - prev.adds} 계약 추가",
                    tag=f"{run}:{record.trade_id}:add",
                )
            )
    amount = realized - state.realized if len(closes) == 1 else None
    notes += [_close_note(run, name, r, label_of(r.playbook), amount) for r in closes]

    alerts = {code: at for code, at in fresh.alerts.items() if code in errors}
    for code, detail in errors.items():
        last = alerts.get(code)
        if last is None or now - last >= ALERT_EVERY:
            notes.append(
                Note(
                    kind="alert",
                    title=f"⚠️ 경보 · {name}",
                    body=f"{code} — {detail[:140]}",
                    tag=f"{run}:alert:{code}",
                )
            )
            alerts[code] = now
    fresh.alerts = alerts
    return notes, fresh
