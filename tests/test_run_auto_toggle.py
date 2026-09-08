"""콘솔의 **중지·재개** — 새 진입만 여닫는다 (사용자 요구 2026-08-20).

Note:
    🔴 **`paused` 를 쓰면 안 된다.** 저쪽은 걸음(`Session.step`) 자체를 멈추는데,
    걸음이 멈추면 그 뒤에 딸린 것이 전부 멈춘다:

    ```
    _guard_stop      손절 재장착 — Gate 조건부는 24시간에 조용히 만료된다
    reconcile        거래소가 먼저 닫았는지 확인
    _apply_half      원장이 반익했으면 거래소에서도 던다
    _retry_ladders   못 건 익절을 다시 건다
    ```

    ⇒ 포지션을 든 채로 `paused` 를 걸면 **아무도 지키지 않는 포지션**이 된다.

    ⭐ `auto` 는 새 진입만 막는다 — 증거금이 바닥났을 때 러너가 스스로 하는 것과 같은
    조치다 (`live_margin_exhausted`). 리스크를 줄이는 행동은 막지 않는다 (spec §1.2.1).
"""

from __future__ import annotations

import inspect

from updown.orchestration.walkforward.live_runner import LiveRunner
from updown.orchestration.walkforward.session import Session


class TestAutoIsNotPause:
    def test_stopping_entries_does_not_stop_the_step(self) -> None:
        """🔴 걸음이 멈추면 손절 관리까지 멈춘다 — 그래서 `auto` 여야 한다."""
        source = inspect.getsource(Session.step)
        # ⚠️ 두 문장이 **다른 자리**에 있어야 한다: `paused` 는 걸음을 접고,
        #    `auto` 는 진입만 접는다.
        assert "self.paused" in source
        # ⚠️ 스위치는 `_may_enter` 로 모였다 (2026-08-30 `reconciled` 추가). 서식이
        #    아니라 **`auto` 가 진입 문에 있는가**를 본다 — 그것이 이 시험의 뜻이다.
        gate = inspect.getsource(Session._may_enter)  # pyright: ignore[reportPrivateUsage]
        assert "self.auto" in gate

    def test_the_guards_live_below_the_step(self) -> None:
        """⛔ `step()` 이 None 을 주면 그 아래가 통째로 안 돈다 — 그것이 위험의 근거다."""
        source = inspect.getsource(LiveRunner._walk_once)  # pyright: ignore[reportPrivateUsage]
        cut = source.index("if shot is None")
        for name in ("_guard_stop", "reconcile", "_apply_half", "_retry_ladders"):
            assert source.index(name) > cut, f"{name} 이 걸음 위에 있다 — 검사가 뜻을 잃는다"

    def test_the_endpoint_moves_auto_not_paused(self) -> None:
        """🔴 배선이 틀리면 "중지" 가 포지션을 무방비로 만든다."""
        from updown.apps.api.walkforward import set_auto

        source = inspect.getsource(set_auto)
        assert "session.auto" in source
        assert "paused" not in source.split('"""')[-1], "본문이 paused 를 건드린다"

    def test_the_endpoint_does_not_close_anything(self) -> None:
        """⚠️ 닫는 것은 삭제의 일이다 — 한 단추에 묶으면 "잠깐 멈춤" 이 되돌릴 수 없어진다."""
        from updown.apps.api.walkforward import set_auto

        body = inspect.getsource(set_auto).split('"""')[-1]
        for word in ("close", "drop", "cancel"):
            assert word not in body, f"중지가 {word} 를 부른다"

    def test_the_summary_reports_it(self) -> None:
        """⛔ 화면이 지금 상태를 모르면 단추가 무엇을 할지 사람이 못 읽는다 (규칙 #8)."""
        from updown.apps.api.walkforward import _summary  # pyright: ignore[reportPrivateUsage]

        assert '"auto": session.auto' in inspect.getsource(_summary)


class TestDefault:
    def test_a_fresh_session_takes_entries(self) -> None:
        """⭐ 기본은 켜짐이다 — 띄우자마자 안 도는 판은 고장과 구별되지 않는다.

        ⚠️ `slots` 라 클래스 속성이 슬롯 서술자다 — 선언된 기본값을 봐야 한다.
        """
        import dataclasses

        found = {item.name: item.default for item in dataclasses.fields(Session)}
        assert found["auto"] is True
        assert found["paused"] is False
