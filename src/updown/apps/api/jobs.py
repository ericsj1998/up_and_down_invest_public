"""오래 걸리는 작업을 **연결과 분리**해 돌린다 (Phase 5 §5-6).

## 왜 필요한가 — 창을 닫으면 분석이 죽었다

처음 구현은 SSE 응답 생성기 안에서 분석 태스크를 만들고, 생성기가 닫힐 때
`task.cancel()` 했다. 그래서:

| 사용자가 한 일 | 일어난 일 |
|---|---|
| 탭을 닫는다 | 분석 취소. 원장에 아무것도 안 남는다 |
| 새로고침한다 | 같음. 처음부터 다시 |
| 네트워크가 잠깐 끊긴다 | 같음 |

분석은 최대 180초(모델 타임아웃)가 걸리고 **원장 저장은 맨 끝**에 일어난다. 즉 179초를
기다린 뒤 창을 닫으면 그 179초와 10종의 호출 비용이 통째로 사라졌다.

## 구조 — 작업이 주인이고 연결은 구경꾼이다

```
POST /ai/analyze        → 작업 생성 + 즉시 job_id 반환 (기다리지 않는다)
GET  /ai/jobs/{id}/events → 지금까지의 로그를 **재생**한 뒤 live 구독
```

작업 태스크는 레지스트리가 들고 있으므로 구독자가 전부 떠나도 계속 돈다. 다시 붙으면
`lines` 에 쌓인 로그를 처음부터 받아 화면이 이어진다.

## 🔴 프로세스 메모리에 있다

작업 목록은 이 프로세스 안에만 있다. 재기동하면 사라지고, 워커가 여럿이면 공유되지
않는다. 지금은 단일 워커 개발 서버라 충분하며, **결과의 영속성은 원장이 책임진다** —
작업이 끝나면 `logs/ai_experiment/` 에 남으므로 레지스트리가 사라져도 분석 자체는
남는다. 레지스트리는 "진행 중인 것을 따라가는" 용도지 저장소가 아니다.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from updown.common.logging.setup import get_logger

_logger = get_logger("apps.api.jobs")

MAX_JOBS = 20
KIND_LIMIT = 2
"""같은 종류의 작업이 동시에 도는 상한 — 넘으면 429 (보안 점검 #7 · 2026-09-11).

모델 호출 작업 하나가 수 분이라, 단추를 연타하면 NVIDIA 요율과 이벤트 루프를 같이 먹는다.
한 사람의 화면이 두 개를 넘게 띄울 일은 없다."""
"""보관할 작업 수. 넘으면 **끝난 것부터** 버린다 — 도는 작업을 버리면 추적이 끊긴다."""

type Reporter = Callable[[str], None]
"""진행 한 줄을 알리는 콜백."""


@dataclass
class Job:
    """오래 걸리는 작업 하나.

    Attributes:
        job_id: 식별자.
        kind: 작업 종류 (화면 표시용).
        label: 사람이 읽는 대상 (`KRW-BTC · 10종`).
        started_at: 시작 시각 (UTC).
        lines: 진행 로그 **전체**. 재접속한 구독자에게 처음부터 재생한다.
        result: 성공 결과. 아직이거나 실패면 None.
        error: 실패 사유. 성공이면 빈 문자열.
        done: 끝났는가.
        task: 실행 중인 태스크.
        waiters: 지금 붙어 있는 구독자 큐들.
    """

    job_id: str
    kind: str
    label: str
    started_at: datetime
    lines: list[str] = field(default_factory=list[str])
    result: dict[str, Any] | None = None
    error: str = ""
    done: bool = False
    task: asyncio.Task[None] | None = None
    waiters: list[asyncio.Queue[tuple[str, dict[str, Any]]]] = field(
        default_factory=list[asyncio.Queue[tuple[str, dict[str, Any]]]]
    )

    def publish(self, name: str, data: dict[str, Any]) -> None:
        """구독자 전원에게 사건 하나를 보낸다.

        Args:
            name: 이벤트 이름 (`progress` / `result` / `error`).
            data: 본문.

        Note:
            구독자가 없어도 **버리지 않는다** — `lines` 에 남겨 두었다가 나중에 붙는
            사람에게 재생한다. 그것이 이 구조의 요점이다.
        """
        if name == "progress":
            self.lines.append(str(data.get("line", "")))
        for queue in list(self.waiters):
            queue.put_nowait((name, data))

    def snapshot(self) -> dict[str, Any]:
        """목록 화면용 요약.

        Returns:
            직렬화용 dict.
        """
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "label": self.label,
            "started_at": self.started_at.isoformat(),
            "lines": len(self.lines),
            "done": self.done,
            "error": self.error,
            "ok": self.done and not self.error,
        }


class JobRegistry:
    """진행 중·최근 끝난 작업들.

    Note:
        락을 쓰지 않는다 — 단일 이벤트 루프에서만 만지고, `await` 를 사이에 두지 않는
        짧은 구간만 수정하므로 경합이 없다. 스레드에서 부르면 그 전제가 깨진다.
    """

    def __init__(self, limit: int = MAX_JOBS) -> None:
        """레지스트리를 만든다.

        Args:
            limit: 보관 상한.
        """
        self._limit = limit
        self._jobs: dict[str, Job] = {}

    def running(self, kind: str, label: str) -> Job | None:
        """같은 종류·같은 대상으로 **아직 도는** 작업.

        Args:
            kind: 작업 종류.
            label: 표시용 대상 — 같으면 같은 일이다.

        Returns:
            도는 작업 또는 None. 부르는 쪽은 새로 띄우지 않고 이것을 준다 — 단추 연타·새로고침이
            같은 분석을 둘 띄우고 셋째부터 429 를 맞았다(2026-09-11 신고).
        """
        for item in self._jobs.values():
            if item.kind == kind and item.label == label and not item.done:
                return item
        return None

    def start(
        self,
        kind: str,
        label: str,
        work: Callable[[Reporter], Awaitable[dict[str, Any]]],
    ) -> Job:
        """작업을 띄우고 **즉시** 돌려준다.

        Args:
            kind: 작업 종류.
            label: 표시용 대상.
            work: 진행 보고 콜백을 받아 결과 dict 를 내는 코루틴 함수.

        Returns:
            생성된 작업. 태스크는 이미 돌고 있다.

        Raises:
            HTTPException: 같은 종류가 이미 `KIND_LIMIT` 개 돌고 있으면 429.

        Note:
            🔴 태스크 참조를 **레지스트리가 들고 있어야** 한다. 지역 변수로 두면
            가비지 컬렉터가 도중에 회수할 수 있고(파이썬 문서의 알려진 함정), 그러면
            분석이 조용히 사라진다 — 우리가 고치려던 증상과 똑같이 보인다.
        """
        running = sum(1 for item in self._jobs.values() if item.kind == kind and not item.done)
        if running >= KIND_LIMIT:
            raise HTTPException(429, f"{kind} 작업이 이미 {running}개 돌고 있다 — 끝난 뒤 다시")
        job = Job(
            job_id=uuid4().hex[:12],
            kind=kind,
            label=label,
            started_at=datetime.now(UTC),
        )
        self._jobs[job.job_id] = job
        self._evict()

        async def runner() -> None:
            """작업을 돌리고 끝을 알린다.

            Raises:
                asyncio.CancelledError: 취소는 `error` 이벤트로 알린 뒤 그대로 올린다 — 삼키면
                    이벤트 루프 종료가 막힌다.
            """
            try:
                job.result = await work(lambda line: job.publish("progress", {"line": line}))
                job.publish("result", job.result)
            except asyncio.CancelledError:
                job.error = "취소됨"
                job.publish("error", {"detail": job.error})
                raise
            except Exception as exc:
                # ⛔ 조용히 삼키지 않는다 (절대 규칙 #8). 작업 하나가 실패해도 서버는
                #    살아 있어야 하지만, 실패 사실은 화면까지 올라가야 한다. 넓게 잡는
                #    이유는 여기 들어오는 것이 **임의의 작업**이라 예외 계약이 없어서다.
                job.error = str(exc)
                _logger.info("job_failed", payload={"job_id": job.job_id, "detail": job.error})
                job.publish("error", {"detail": job.error})
            finally:
                job.done = True
                # 끝났다는 것을 대기 중인 구독자에게 알린다 — 안 그러면 영원히 기다린다.
                for queue in list(job.waiters):
                    queue.put_nowait(("done", {}))

        job.task = asyncio.create_task(runner())
        _logger.info("job_started", payload={"job_id": job.job_id, "kind": kind, "label": label})
        return job

    def get(self, job_id: str) -> Job | None:
        """작업 하나를 찾는다.

        Args:
            job_id: 식별자.

        Returns:
            작업. 없거나 이미 버려졌으면 None.
        """
        return self._jobs.get(job_id)

    def recent(self) -> list[Job]:
        """최근 작업들 — **도는 것이 먼저**.

        Returns:
            작업들.
        """
        return sorted(
            self._jobs.values(),
            key=lambda item: (item.done, -item.started_at.timestamp()),
        )

    def _evict(self) -> None:
        """상한을 넘으면 **끝난 것부터** 버린다."""
        if len(self._jobs) <= self._limit:
            return
        finished = [item for item in self._jobs.values() if item.done]
        finished.sort(key=lambda item: item.started_at)
        for item in finished[: len(self._jobs) - self._limit]:
            del self._jobs[item.job_id]


#: 프로세스 하나가 공유하는 레지스트리 (모듈 docstring 의 한계 참조).
registry = JobRegistry()
