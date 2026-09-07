"""감사 로그가 **누가 했는지** 남기는가 (2026-08-30 외부 공개 준비).

## 왜 필요한가

`trace_id` 는 한 흐름을 잇지만 그 흐름을 **누가 시작했는지**는 안 말해 준다. 혼자 쓸
때는 답이 하나뿐이라 문제가 없었는데, 외부에 열고 사람이 여럿 들어오는 순간
*"이 주문 누가 냈지"* 를 답할 수 없게 된다.

## ⛔ 없을 때 지어내지 않는다

엔진의 스케줄 잡·부팅 로그는 사람이 시작한 것이 아니다. 거기에 값을 채우면
**"누가 했는지 아는 척"** 이 되고, 그건 기록이 없는 것보다 나쁘다.
"""

import inspect
from pathlib import Path

from updown.common.logging.context import actor_context, get_actor, trace_context


class TestTheActorFollowsTheFlow:
    def test_it_is_empty_outside_a_request(self) -> None:
        """⛔ 기계가 한 일에 사람 이름이 붙으면 안 된다."""
        assert get_actor() is None

    def test_it_is_visible_inside(self) -> None:
        with actor_context("a@b.com"):
            assert get_actor() == "a@b.com"

    def test_it_is_restored_afterwards(self) -> None:
        """🔴 다음 요청의 로그에 앞사람 이메일이 새면 감사 기록이 **거짓말**이 된다."""
        with actor_context("first@b.com"):
            pass
        assert get_actor() is None

    def test_nesting_restores_the_outer_one(self) -> None:
        with actor_context("outer@b.com"):
            with actor_context("inner@b.com"):
                assert get_actor() == "inner@b.com"
            assert get_actor() == "outer@b.com"

    def test_none_means_none(self) -> None:
        """엔진 잡은 `None` 을 넣는다 — 그때도 앞 값이 새면 안 된다."""
        with actor_context("someone@b.com"), actor_context(None):
            assert get_actor() is None

    def test_it_is_independent_of_the_trace(self) -> None:
        """둘은 **다른 질문**이다 — 흐름을 잇는 것과 사람을 아는 것."""
        with trace_context("abc123"):
            assert get_actor() is None


class TestItReachesTheRow:
    def test_the_record_carries_it(self) -> None:
        import uuid

        from updown.common.db.models.enums import LogLevel
        from updown.common.logging.event_sink import EventRecord

        made = EventRecord(
            event_id=uuid.uuid4(),
            trace_id="t",
            module="m",
            level=LogLevel.INFO,
            event_type="E",
            actor="a@b.com",
        )
        assert made.actor == "a@b.com"
        # ⚠️ 폴백 파일에도 남아야 한다 — DB 가 죽었을 때 남는 유일한 기록이고,
        #    하필 그때가 "누가 했나" 를 제일 알고 싶은 순간이다.
        assert made.to_json_dict()["actor"] == "a@b.com"

    def test_the_insert_writes_the_column(self) -> None:
        import updown.common.logging.event_sink as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "actor" in source.split("_INSERT = ")[1].split('""")')[0]
        assert '"actor": record.actor,' in source

    def test_the_audit_logger_reads_the_context(self) -> None:
        """🔴 컬럼만 있고 아무도 안 채우면 **조용히 NULL** 이다."""
        from updown.common.logging.audit import AuditLogger

        source = inspect.getsource(AuditLogger.record)
        assert "actor=get_actor()" in source


class TestTheGuardBindsIt:
    def test_every_authenticated_request_carries_the_caller(self) -> None:
        """미들웨어에서 한 번 넣으면 async 경계를 넘어 따라간다."""
        import updown.apps.api.auth as mod

        source = inspect.getsource(mod.guard)
        assert "actor_context(" in source

    def test_it_wraps_the_handler_not_just_the_check(self) -> None:
        """⚠️ 권한 검사만 감싸면 **정작 핸들러가 남기는 로그**에 사람이 안 붙는다."""
        import updown.apps.api.auth as mod

        source = inspect.getsource(mod.guard)
        assert "with actor_context" in source
        assert "_pass(request, call_next" in source


class TestTheColumnIsNullable:
    def test_the_migration_allows_null(self) -> None:
        """⛔ NOT NULL 이면 엔진 잡이 가짜 값을 채우게 된다."""
        source = Path("alembic/versions/0106_event_actor.py").read_text(encoding="utf-8")
        assert "nullable=True" in source

    def test_no_foreign_key_to_accounts(self) -> None:
        """⚠️ 감사 기록은 **그때의 사실**이다 — 계정을 지운다고 사람이 사라지면 안 된다."""
        source = Path("alembic/versions/0106_event_actor.py").read_text(encoding="utf-8")
        assert "ForeignKey" not in source
        assert "accounts.email" not in source
