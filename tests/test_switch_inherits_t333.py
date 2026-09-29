"""매매법 전환이 앞 세션의 열린 원장 기록을 물려준다 (T333 · 2026-09-30 사고 셋의 뿌리)."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from updown.apps.api import rebalancer as mod
from updown.apps.api import walkforward as wf
from updown.orchestration.walkforward.ledger import Outcome


class TestOpenRecordsOf:
    def test_no_handle_means_nothing(self) -> None:
        assert mod.open_records_of(None) == ()
        assert mod.open_records_of("") == ()

    def test_unknown_session_means_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(mod, "SESSIONS", {})
        assert mod.open_records_of("live-none") == ()

    def test_only_open_records_are_copied(self, monkeypatch) -> None:
        from tests.test_live_adopt import FakeSession  # 최소 세션 — 원장 기록 목록만 쓴다

        opened = SimpleNamespace(outcome=Outcome.OPEN, trade_id="a")
        closed = SimpleNamespace(outcome=Outcome.STOPPED, trade_id="b")
        session = FakeSession()
        session.ledger.records.extend([opened, closed])  # type: ignore[attr-defined]
        monkeypatch.setattr(mod, "SESSIONS", {"live-x": SimpleNamespace(session=session)})
        monkeypatch.setattr(
            mod, "replace", lambda item: item
        )  # SimpleNamespace 는 dataclass 가 아니다
        got = mod.open_records_of("live-x")
        assert [item.trade_id for item in got] == ["a"]


class TestWiring:
    def test_switch_takes_records_before_dropping_the_old_runner(self) -> None:
        source = inspect.getsource(mod._switch_playbook)  # pyright: ignore[reportPrivateUsage]
        assert source.index("inherited = open_records_of(old)") < source.index(
            "await _drop_one(old)"
        )
        assert "inherit=inherited" in source

    def test_spawn_passes_inherit_through(self) -> None:
        source = inspect.getsource(mod._spawn_session)  # pyright: ignore[reportPrivateUsage]
        assert "inherit=inherit" in source

    def test_live_start_inherits_before_the_runner_is_born(self) -> None:
        """러너가 태어날 때 원장에 있어야 `_revived_open` 에 들어간다 — 진입을 다시 안 보낸다."""
        source = Path(wf.__file__).read_text(encoding="utf-8")
        body = source.split("async def _live_start(")[1]
        assert body.index("elif inherit:") < body.index("runner = LiveRunner(")
        assert "session.ledger.records.extend(taken)" in body
        assert "session.adopt(taken[0])" in body
