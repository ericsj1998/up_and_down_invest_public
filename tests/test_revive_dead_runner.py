"""죽은 판은 스스로를 막지 않고 되살아난다 · 배경 루프가 죽으면 말한다 (T852 F3).

지키는 것:

- 러너 태스크가 **끝난** 판은 되살리기 직전에 도는 판 목록에서 빠진다 — 안 빼면 같은 키로
  다시 띄울 때 "이 종목은 이미 도는 판이 있다" 에 자기 자신이 걸린다.
- 태스크가 아직 도는 판(얼어붙은 판 포함)은 안 뺀다 — 빼고 새로 띄우면 판이 둘이 된다.
- 배경 루프가 예외로 끝나면 사건 · 안전장치 목록에 남는다. 취소 · 정상 반환은 조용하다.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.apps.api import walkforward as api
from updown.orchestration.walkforward.live_runner import LiveRunner


class Task:
    """끝났는지만 답하는 태스크 대역."""

    def __init__(self, *, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


def board(monkeypatch: pytest.MonkeyPatch, *, done: bool | None) -> dict[str, Any]:
    runners: dict[str, Any] = {"k": object()}
    entry = SimpleNamespace(runner=None if done is None else Task(done=done))
    monkeypatch.setattr(api, "LIVE_RUNNERS", runners)
    monkeypatch.setattr(api, "SESSIONS", {"k": entry})
    return runners


def test_a_board_whose_task_ended_leaves_the_running_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runners = board(monkeypatch, done=True)
    assert api.retire_dead_runner("k") is True
    assert "k" not in runners
    assert "k" in api.SESSIONS, "세션 항목은 새 판이 덮어쓸 때까지 둔다(펀드 저장이 몫을 읽는다)"
    assert api.retire_dead_runner("k") is False, "두 번째는 뺄 것이 없다"


def test_a_board_that_is_still_running_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    runners = board(monkeypatch, done=False)
    assert api.retire_dead_runner("k") is False
    assert "k" in runners, "얼어붙은 판을 빼고 새로 띄우면 같은 종목에 판이 둘이 된다"


def test_a_board_without_a_task_or_an_unknown_key_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runners = board(monkeypatch, done=None)
    assert api.retire_dead_runner("k") is False and "k" in runners
    assert api.retire_dead_runner("없는 키") is False


def test_revive_retires_the_dead_runner_before_starting_again() -> None:
    source = inspect.getsource(api._revive)  # pyright: ignore[reportPrivateUsage]
    assert source.index("retire_dead_runner(key)") < source.index("await _live_start(")


class Log:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, name: str, **_: object) -> None:
        self.errors.append(name)

    def info(self, name: str, **_: object) -> None:
        del name


class Made:
    """`_helper_done` 이 만지는 것만 가진 러너 — 실제 메서드를 돌린다."""

    _helper_done = LiveRunner._helper_done  # pyright: ignore[reportPrivateUsage]
    _fired = LiveRunner._fired  # pyright: ignore[reportPrivateUsage]

    def __init__(self) -> None:
        self._log = Log()
        self.guards: dict[str, dict[str, str]] = {}
        self.last_error = ""


async def ended(kind: str) -> asyncio.Task[None]:
    async def body() -> None:
        if kind == "raise":
            raise RuntimeError("조회가 터졌다")
        if kind == "hang":
            await asyncio.Event().wait()

    task = asyncio.create_task(body(), name="live-probe")
    await asyncio.sleep(0)
    if kind == "hang":
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return task


def test_a_helper_that_died_is_reported() -> None:
    made = Made()
    task = asyncio.run(ended("raise"))
    cast("Any", made)._helper_done(task)
    assert made._log.errors == ["live_helper_dead"]  # pyright: ignore[reportPrivateUsage]
    assert "helper_dead" in made.guards and "live-probe" in made.guards["helper_dead"]["why"]
    assert "live-probe" in made.last_error


def test_a_cancelled_or_finished_helper_is_quiet() -> None:
    for kind in ("hang", "return"):
        made = Made()
        cast("Any", made)._helper_done(asyncio.run(ended(kind)))
        assert made._log.errors == [] and made.guards == {} and made.last_error == ""  # pyright: ignore[reportPrivateUsage]


def test_every_helper_is_watched() -> None:
    source = inspect.getsource(LiveRunner.run)
    assert "task.add_done_callback(self._helper_done)" in source
    assert source.index("add_done_callback") < source.index("await self._loop()")
