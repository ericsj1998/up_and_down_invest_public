"""engine 생존 신호 — 데드맨 스위치 (P0-9-2 · spec §12.6).

## `pgrep` 으로는 부족하다

engine 은 컨테이너의 **PID 1** 이다. 프로세스가 죽으면 컨테이너 자체가 종료되므로
"프로세스가 살아 있는가"를 컨테이너 안에서 확인하는 것은 거의 항진명제다.

정작 위험한 상태는 **프로세스는 살아 있는데 이벤트 루프가 멈춘 것**이다 — 블로킹 호출이
루프를 잡거나 데드락이 걸리면 잡이 하나도 안 도는데 컨테이너는 계속 running 이다.
그 상태에서 손절 감시가 멈춰 있어도 아무도 모른다.

## 그래서 루프 안에서 파일을 갱신한다

```
engine 이벤트 루프 → 주기적으로 파일 mtime 갱신 → healthcheck 가 신선도 확인
```

루프가 멈추면 갱신이 멈추고, mtime 이 오래되면 healthcheck 가 실패한다. **루프가 도는지를
루프 자신이 증명하는** 구조다 (§12.6 "감시자의 감시").

## 리더가 아닌 인스턴스도 살아 있다

락을 기다리는 대기 인스턴스는 잡을 실행하지 않지만 **정상**이다 (P0-9-4) — 리더가 죽으면
승격할 페일오버 수단이기 때문이다. 그래서 이 신호는 **락 보유와 무관하게** 갱신된다.
락 보유를 healthcheck 로 삼으면 대기 인스턴스가 unhealthy 로 재시작되어 페일오버가
불가능해진다.
"""

import asyncio
import contextlib
import time
from pathlib import Path

from updown.common.logging.setup import get_logger
from updown.common.paths import under

_logger = get_logger("apps.engine.liveness")

DEFAULT_LIVENESS_PATH = under("engine_alive")
"""생존 신호 파일. `logs/` 는 컨테이너에서 쓰기 가능하고 gitignore 대상이다."""

DEFAULT_TOUCH_INTERVAL_SECONDS = 5.0
"""갱신 주기."""

DEFAULT_STALE_AFTER_SECONDS = 30.0
"""이 시간 넘게 갱신이 없으면 죽은 것으로 본다.

갱신 주기의 6배다. 일시적 지연(GC, 무거운 잡)으로 healthcheck 가 실패하면 컨테이너가
재시작되고, 그 재시작이 진행 중인 잡을 끊는다 — 여유를 넉넉히 둔다.
"""


class LivenessBeacon:
    """이벤트 루프가 살아 있음을 파일로 알린다.

    Note:
        `touch` 만 한다 — 내용을 쓰지 않는다. mtime 이 신호이고, 내용을 두면 무엇을
        믿어야 하는지가 둘로 갈린다.
    """

    def __init__(
        self,
        path: Path = DEFAULT_LIVENESS_PATH,
        *,
        interval_seconds: float = DEFAULT_TOUCH_INTERVAL_SECONDS,
    ) -> None:
        """비컨을 만든다.

        Args:
            path: 신호 파일 경로.
            interval_seconds: 갱신 주기.
        """
        self._path = path
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def path(self) -> Path:
        """신호 파일 경로."""
        return self._path

    def touch(self) -> None:
        """신호 파일의 mtime 을 지금으로 갱신한다.

        Note:
            실패해도 예외를 올리지 않는다. 생존 신호를 못 쓰는 것은 문제지만, 그것 때문에
            engine 이 죽으면 **감시 수단을 잃으려다 감시 대상까지 잃는다.** 대신 로그를 남겨
            healthcheck 가 곧 실패하게 둔다 (spec §7 — 조용히 넘기지 않는다).
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
        except OSError as exc:
            _logger.warning(
                "engine_liveness_touch_failed",
                payload={"path": str(self._path), "reason": str(exc)},
            )

    async def start(self) -> None:
        """갱신 루프를 띄운다."""
        if self._task is not None and not self._task.done():
            return
        self.touch()  # 기동 직후 healthcheck 가 stale 로 보지 않게 먼저 한 번
        self._task = asyncio.create_task(self._loop())
        _logger.info(
            "engine_liveness_started",
            payload={"path": str(self._path), "interval_seconds": self._interval},
        )

    async def stop(self) -> None:
        """갱신 루프를 멈추고 신호 파일을 지운다.

        Note:
            파일을 지우는 이유: 정상 종료 후 컨테이너가 재생성될 때 **옛 mtime 이 남아
            있으면** 아직 안 뜬 engine 을 살아 있다고 오판할 수 있다. named volume 을
            쓰므로 파일이 컨테이너보다 오래 산다.
        """
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)

    async def _loop(self) -> None:
        """주기적으로 갱신한다."""
        while True:
            await asyncio.sleep(self._interval)
            self.touch()


def is_alive(
    path: Path = DEFAULT_LIVENESS_PATH,
    *,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    now: float | None = None,
) -> bool:
    """신호 파일이 신선한지 판정한다 (healthcheck 가 부른다).

    Args:
        path: 신호 파일 경로.
        stale_after_seconds: 이 시간 넘게 갱신이 없으면 죽은 것으로 본다.
        now: 기준 시각 (epoch 초). None 이면 현재. **인자로 받는다** — 테스트가 임의
            시점을 재현할 수 있어야 한다 (원칙 P1).

    Returns:
        살아 있으면 True.

    Note:
        파일이 없으면 False 다. "아직 안 만들어졌다"와 "죽었다"를 구분하지 않는 이유:
        둘 다 "지금 engine 루프가 돈다는 증거가 없다"이고, healthcheck 의 `start_period` 가
        기동 유예를 담당한다.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return (now if now is not None else time.time()) - mtime <= stale_after_seconds


def main() -> int:
    """Healthcheck 진입점 — `python -m updown.apps.engine.liveness`.

    Returns:
        살아 있으면 0, 아니면 1 (컨테이너 healthcheck 규약).
    """
    return 0 if is_alive() else 1


if __name__ == "__main__":
    raise SystemExit(main())
