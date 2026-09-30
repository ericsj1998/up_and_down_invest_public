"""돌던 펀드의 매매법 자동 대체(T332) — `superseded_by` 선언 · 마감 밖 창 · 화면과 같은 몸통."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from updown.analysis.playbook.select import load_playbooks
from updown.apps.api import rebalancer as mod


class TestDeclaration:
    def test_220_is_superseded_by_230(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        assert books["private_strategy"].superseded_by == "private_strategy"
        # 2.4.0 (1.28.0 · 2026-09-30)
        assert books["private_strategy"].superseded_by == "private_strategy"
        assert books["private_strategy"].superseded_by is None
        assert books["private_strategy"].listed is True
        assert books["private_strategy"].listed is False

    def test_only_the_live_bundle_declares_it(self) -> None:
        declared = {b.playbook_id for b in load_playbooks() if b.superseded_by}
        assert declared == {"private_strategy", "private_strategy"}


class TestSupersedingPlaybook:
    def test_follows_the_declaration(self) -> None:
        books = load_playbooks()
        assert (
            mod.superseding_playbook("private_strategy", books)
            == "private_strategy"
        )
        nxt = mod.superseding_playbook("private_strategy", books)
        assert nxt == "private_strategy"
        assert mod.superseding_playbook("private_strategy", books) is None
        assert mod.superseding_playbook("no_such_playbook", books) is None

    def test_a_missing_target_means_stay(self) -> None:
        books = load_playbooks()
        base = next(b for b in books if b.playbook_id == "private_strategy")
        odd = replace(base, superseded_by="not_declared_anywhere")
        assert mod.superseding_playbook("private_strategy", [odd]) is None

    def test_a_chain_is_followed_and_a_cycle_is_cut(self) -> None:
        books = load_playbooks()
        base = next(b for b in books if b.playbook_id == "private_strategy")
        a = replace(base, playbook_id="a", superseded_by="b")
        b = replace(base, playbook_id="b", superseded_by="c")
        c = replace(base, playbook_id="c", superseded_by=None)
        assert mod.superseding_playbook("a", [a, b, c]) == "c"
        loop_a = replace(base, playbook_id="a", superseded_by="b")
        loop_b = replace(base, playbook_id="b", superseded_by="a")
        assert mod.superseding_playbook("a", [loop_a, loop_b]) in ("b", None)
        me = replace(base, playbook_id="a", superseded_by="a")
        assert mod.superseding_playbook("a", [me]) is None


class TestDelay:
    """전환(약 2.5분)이 정시 마감에 걸치지 않게 — 매시 05 ~ 45분 창에서만 시작한다."""

    def test_base_wait_inside_the_window(self) -> None:
        now = datetime(2026, 9, 30, 13, 10, 0, tzinfo=UTC)  # + 10분 = 13:20
        assert mod.supersede_delay_s(now) == mod.SUPERSEDE_BASE_S

    def test_too_close_to_the_next_close_waits_for_the_next_window(self) -> None:
        now = datetime(2026, 9, 30, 13, 40, 0, tzinfo=UTC)  # + 10분 = 13:50 → 14:05
        wait = mod.supersede_delay_s(now)
        at = now.timestamp() + wait
        landed = datetime.fromtimestamp(at, tz=UTC)
        assert (landed.hour, landed.minute) == (14, 5)

    def test_right_after_a_close_waits_until_five_past(self) -> None:
        now = datetime(2026, 9, 30, 13, 52, 0, tzinfo=UTC)  # + 10분 = 14:02 → 14:05
        wait = mod.supersede_delay_s(now)
        landed = datetime.fromtimestamp(now.timestamp() + wait, tz=UTC)
        assert (landed.hour, landed.minute) == (14, 5)


class TestWiring:
    def test_restore_schedules_the_switch(self) -> None:
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "_schedule_supersede()" in source.split("async def fund_retry_loop")[0]

    def test_the_screen_and_the_auto_switch_share_one_body(self) -> None:
        source = Path(mod.__file__).read_text(encoding="utf-8")
        screen = source.split("async def _change_playbook")[1].split("async def _switch_playbook")[
            0
        ]
        auto = source.split("async def _supersede_funds")[1].split(
            '@router.post("/{fund_id}/resync")'
        )[0]
        assert "await _switch_playbook(fund, new_pb, payload)" in screen
        assert "await _switch_playbook(fund, target, {})" in auto
        # ⛔ 실패는 옛 매매법으로 계속 — 예외를 밖으로 내지 않는다(리더 기동을 안 막는다)
        assert "fund_playbook_supersede_failed" in auto
