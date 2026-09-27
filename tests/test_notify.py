"""웹 푸시 알림 — 새 일만 뽑기(`notify.diff_run`) · 키 · 구독 정리 (사용자 2026-09-27).

🔴 못 박는 것:
  - 처음 본 판은 조용하다(배포 · 재시작 직후 쏟아짐 방지)
  - 대기 → 보유 = 진입 · 보유 → 끝 = 청산(취소는 알리지 않는다) · 불타기 계약이 늘면 한 번
  - 청산 금액은 한 번에 한 건 닫혔을 때만(두 건이면 가를 수 없다)
  - 경보는 error 급만 · 같은 코드 6시간에 한 번 · 기동 잡음(awaiting_fund)은 뺀다
  - VAPID 키는 한 번 만들면 그대로(두 슬롯 · 재시작) · 404/410 구독은 지운다
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from updown.orchestration.notify import ALERT_EVERY, RunState, diff_run
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

T0 = datetime(2026, 9, 27, tzinfo=UTC)


def _rec(tid: str = "t1", **kw: Any) -> TradeRecord:
    base: dict[str, Any] = {
        "trade_id": tid,
        "playbook": "private_strategy@1.1.0",
        "actor": Actor.SYSTEM,
        "placed_at": T0,
        "entry": Decimal(100),
        "planned_stop": Decimal(95),
        "direction": Direction.LONG,
        "outcome": Outcome.PENDING,
    }
    return TradeRecord(**(base | kw))


def _diff(
    records: list[TradeRecord],
    state: RunState | None,
    *,
    realized: Decimal = Decimal(0),
    findings: list[dict[str, str]] | None = None,
    now: datetime = T0,
):
    return diff_run(
        "live1",
        "BTC",
        records,
        realized,
        findings or [],
        state,
        now,
        label_of=lambda attr: {"private_strategy": "돌파 롱"}.get(attr.split("@")[0], attr),
    )


class TestDiffRun:
    def test_first_sight_is_silent(self) -> None:
        notes, state = _diff(
            [_rec(outcome=Outcome.OPEN, opened_at=T0)],
            None,
            findings=[{"code": "wallet_drift", "level": "error", "detail": "x"}],
        )
        assert notes == []
        assert state.trades["t1"].outcome is Outcome.OPEN
        # 이미 떠 있던 경보도 보낸 것으로 친다 — 재시작마다 다시 울리지 않게.
        assert "wallet_drift" in state.alerts

    def test_pending_to_open_is_entry(self) -> None:
        pending = _rec()
        _, state = _diff([pending], None)
        notes, _ = _diff([replace(pending, outcome=Outcome.OPEN, opened_at=T0)], state)
        assert [n.kind for n in notes] == ["entry"]
        assert notes[0].title == "진입 · BTC 롱"
        assert "돌파 롱" in notes[0].body and "손절 95" in notes[0].body

    def test_new_record_already_open_is_entry(self) -> None:
        _, state = _diff([], None)
        notes, _ = _diff([_rec(outcome=Outcome.OPEN, direction=Direction.SHORT)], state)
        assert [n.title for n in notes] == ["진입 · BTC 숏"]

    def test_close_with_amount(self) -> None:
        open_ = _rec(outcome=Outcome.OPEN, opened_at=T0)
        _, state = _diff([open_], None, realized=Decimal(10))
        closed = replace(
            open_,
            outcome=Outcome.TAKE_PROFIT,
            exit_price=Decimal(110),
            closed_at=T0 + timedelta(hours=3),
        )
        notes, state2 = _diff([closed], state, realized=Decimal("22.5"))
        assert [n.kind for n in notes] == ["close"]
        assert notes[0].good is True
        assert notes[0].title.startswith("✅ 목표 익절 · BTC 롱")
        assert "+12.50 USDT" in notes[0].body
        assert state2.realized == Decimal("22.5")

    def test_stop_is_bad(self) -> None:
        open_ = _rec(outcome=Outcome.OPEN, opened_at=T0)
        _, state = _diff([open_], None)
        closed = replace(open_, outcome=Outcome.STOP_LOSS, exit_price=Decimal(95), closed_at=T0)
        notes, _ = _diff([closed], state, realized=Decimal(-5))
        assert notes[0].good is False
        assert notes[0].title.startswith("🔴 ")

    def test_cancelled_pending_is_silent(self) -> None:
        pending = _rec()
        _, state = _diff([pending], None)
        notes, _ = _diff([replace(pending, outcome=Outcome.CANCELLED)], state)
        assert notes == []

    def test_two_closes_have_no_amount(self) -> None:
        a = _rec("a", outcome=Outcome.OPEN)
        b = _rec("b", outcome=Outcome.OPEN)
        _, state = _diff([a, b], None)
        notes, _ = _diff(
            [
                replace(a, outcome=Outcome.STOP_LOSS, exit_price=Decimal(95)),
                replace(b, outcome=Outcome.TAKE_PROFIT, exit_price=Decimal(110)),
            ],
            state,
            realized=Decimal(7),
        )
        assert len(notes) == 2
        assert all("USDT" not in n.body for n in notes)

    def test_add_once(self) -> None:
        open_ = _rec(outcome=Outcome.OPEN)
        _, state = _diff([open_], None)
        added = replace(open_, add_contracts=3)
        notes, state = _diff([added], state)
        assert [n.kind for n in notes] == ["add"]
        assert "3 계약" in notes[0].body
        notes, _ = _diff([added], state)
        assert notes == []

    def test_alert_rate_limit_and_quiet(self) -> None:
        _, state = _diff([], None)
        bad = [
            {"code": "wallet_drift", "level": "error", "detail": "원장과 지갑이 갈렸다"},
            {"code": "awaiting_fund", "level": "error", "detail": "기동 잡음"},
            {"code": "slow", "level": "warn", "detail": "경고급"},
        ]
        notes, state = _diff([], state, findings=bad)
        assert [n.tag for n in notes] == ["live1:alert:wallet_drift"]
        notes, state = _diff([], state, findings=bad, now=T0 + timedelta(hours=1))
        assert notes == []
        notes, state = _diff([], state, findings=bad, now=T0 + ALERT_EVERY)
        assert len(notes) == 1

    def test_alert_clears_and_realerts(self) -> None:
        _, state = _diff([], None)
        bad = [{"code": "wallet_drift", "level": "error", "detail": "x"}]
        notes, state = _diff([], state, findings=bad)
        assert len(notes) == 1
        _, state = _diff([], state, findings=[], now=T0 + timedelta(minutes=1))
        notes, _ = _diff([], state, findings=bad, now=T0 + timedelta(minutes=2))
        assert len(notes) == 1


class TestKeyAndSubs:
    @pytest.fixture(autouse=True)
    def _logs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UPDOWN_LOGS_DIR", str(tmp_path))
        from updown.apps.api import notify as mod

        monkeypatch.setattr(mod, "_mode", lambda: "demo")

    def test_key_persists_and_public_is_65_bytes(self) -> None:
        from base64 import urlsafe_b64decode

        from updown.apps.api import notify as mod

        first = mod.public_key_b64(mod.load_or_create_key())
        second = mod.public_key_b64(mod.load_or_create_key())
        assert first == second
        raw = urlsafe_b64decode(first + "=" * (-len(first) % 4))
        assert len(raw) == 65 and raw[0] == 4
        assert oct(mod.key_path().stat().st_mode & 0o777) == "0o600"

    @pytest.mark.asyncio
    async def test_gone_subscriptions_are_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updown.apps.api import notify as mod
        from updown.orchestration.notify import Note

        subs = [
            {"endpoint": "https://push.example/a", "keys": {"p256dh": "p", "auth": "a"}},
            {"endpoint": "https://push.example/b", "keys": {"p256dh": "p", "auth": "a"}},
            {"endpoint": "https://push.example/c", "keys": {"p256dh": "p", "auth": "a"}},
        ]
        mod.subs_path().parent.mkdir(parents=True, exist_ok=True)
        mod.subs_path().write_text(json.dumps(subs), encoding="utf-8")
        codes = {
            "https://push.example/a": None,
            "https://push.example/b": 410,
            "https://push.example/c": 500,
        }

        def fake(sub: dict[str, Any], _data: str, _key: str) -> int | None:
            return codes[sub["endpoint"]]

        monkeypatch.setattr(mod, "_send_one", fake)
        got = await mod.push(Note(kind="test", title="t", body="b", tag="x"))
        assert got == {"sent": 1, "removed": 1, "failed": 1}
        left = [s["endpoint"] for s in json.loads(mod.subs_path().read_text(encoding="utf-8"))]
        assert left == ["https://push.example/a", "https://push.example/c"]

    @pytest.mark.asyncio
    async def test_no_subscribers_sends_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updown.apps.api import notify as mod
        from updown.orchestration.notify import Note

        def boom(*_: Any) -> None:
            raise AssertionError("구독이 없으면 보내지 않는다")

        monkeypatch.setattr(mod, "_send_one", boom)
        got = await mod.push(Note(kind="test", title="t", body="b", tag="x"))
        assert got["sent"] == 0
