"""자원 스냅샷 — 프로세스 · cgroup · 호스트 · 디스크 (T215 · 2026-09-04).

> 사용자: *"배포 시 배포 공간 스펙을 지정하기 위한 RAM·CPU·저장용량 등을 확인할 수 있는 기능."*

## 컨테이너 안에서 무엇을 보나

`psutil.virtual_memory()` 는 `/proc/meminfo` 를 읽어 **호스트 전체**를 말한다. 컨테이너에
메모리 한도(compose `deploy.resources.limits`)가 걸려 있으면 정작 중요한 것은 **cgroup 한도
대비 사용률**이다 — OOM kill 은 호스트가 아니라 cgroup 이 낸다. 그래서 둘 다 낸다:

```
process  이 프로세스 RSS · CPU% · 스레드 · fd          (psutil)
cgroup   memory.current / memory.max · cpu.max          (/sys/fs/cgroup · v2)
host     loadavg 1/5/15 · CPU 수 · 메모리 총량/사용률    (psutil — 호스트 관점)
disks    logs 볼륨 · 루트                               (shutil.disk_usage)
```

순수 함수다 — DB·Redis 를 모른다. 그것들은 api 라우터가 붙인다 (`apps/api/resources_admin.py`).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import psutil

CGROUP_ROOT = Path("/sys/fs/cgroup")
RAM_WARN_PCT = 85.0
DISK_WARN_PCT = 80.0


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def cgroup_memory(root: Path = CGROUP_ROOT) -> dict[str, Any]:
    """Cgroup v2 메모리 — 컨테이너 한도 대비 사용률.

    Args:
        root: cgroup 마운트 경로. 시험에서 가짜 디렉터리를 넣으려고 인자로 열어 둔다.

    Returns:
        `current`·`max`(bytes · 한도가 없거나 cgroup 이 아니면 None)·`percent`(둘 다 있을 때만).
        호스트에서 돌면 파일이 없으므로 전부 None — 그때는 `host_stats` 의 값이 기준이다.
    """
    cur = _read(root / "memory.current")
    lim = _read(root / "memory.max")
    current = int(cur) if cur and cur.isdigit() else None
    limit = int(lim) if lim and lim.isdigit() else None
    pct = (current / limit * 100) if current is not None and limit else None
    return {"current": current, "max": limit, "percent": pct}


def cgroup_cpu(root: Path = CGROUP_ROOT) -> dict[str, Any]:
    """Cgroup v2 CPU — `cpu.max`(quota period) 를 코어 수로 환산한다.

    Args:
        root: cgroup 마운트 경로.

    Returns:
        `{"cores": 한도 코어 수}`. `max`(한도 없음)·파일 없음·형식 이상이면 None — 한도를
        지어내지 않는다.
    """
    raw = _read(root / "cpu.max")
    if not raw:
        return {"cores": None}
    parts = raw.split()
    if parts[0] == "max" or len(parts) < 2:
        return {"cores": None}
    try:
        return {"cores": int(parts[0]) / int(parts[1])}
    except (ValueError, ZeroDivisionError):
        return {"cores": None}


def process_stats(pid: int | None = None) -> dict[str, Any]:
    """이 프로세스(기본) 의 RSS·CPU%·스레드·fd·가동시간.

    Args:
        pid: 볼 프로세스. None 이면 자기 자신.

    Returns:
        `pid`·`rss`·`vms`(bytes)·`cpu_percent`·`threads`·`fds`(플랫폼이 안 주면 None)·`uptime_s`.

    Note:
        `cpu_percent(interval=None)` 은 **직전 호출 이후** 평균이다. 첫 호출은 0 이 나오므로
        폴링(30s)으로 부르는 자리에서 뜻이 있다.
    """
    p = psutil.Process(pid)
    with p.oneshot():
        mem = p.memory_info()
        try:
            fds = p.num_fds()
        except (AttributeError, psutil.Error):
            fds = None
        return {
            "pid": p.pid,
            "rss": int(mem.rss),
            "vms": int(mem.vms),
            "cpu_percent": float(p.cpu_percent(interval=None)),
            "threads": int(p.num_threads()),
            "fds": fds,
            "uptime_s": float(time.time() - p.create_time()),
        }


def host_stats() -> dict[str, Any]:
    """호스트 관점 — loadavg · CPU 수 · 메모리.

    Returns:
        `load`(1/5/15분 · 못 읽으면 None)·`cpu_count`·`mem_total`·`mem_used`(bytes)·`mem_percent`.
        컨테이너 안에서도 **호스트 전체** 값이다 — 한도 대비는 `cgroup_memory` 가 말한다.
    """
    vm = psutil.virtual_memory()
    try:
        l1, l5, l15 = os.getloadavg()
    except OSError:
        l1 = l5 = l15 = None
    return {
        "load": [l1, l5, l15],
        "cpu_count": psutil.cpu_count() or None,
        "mem_total": int(vm.total),
        "mem_used": int(vm.used),
        "mem_percent": float(vm.percent),
    }


def disk_stats(paths: Mapping[str, Path]) -> dict[str, dict[str, Any]]:
    """이름별 디스크 사용량.

    Args:
        paths: `{이름: 경로}`. 예: `{"logs": Path("/app/logs"), "root": Path("/")}`.

    Returns:
        이름별 `total`·`used`·`free`(bytes)·`percent`. 경로가 없으면 `{"missing": True}` 로
        **응답에 남긴다** — 볼륨이 안 붙은 것을 빈 표로 숨기지 않기 위해서다 (규칙 #8).
    """
    out: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        try:
            u = shutil.disk_usage(path)
        except OSError:
            out[name] = {"path": str(path), "missing": True}
            continue
        out[name] = {
            "path": str(path),
            "total": int(u.total),
            "used": int(u.used),
            "free": int(u.free),
            "percent": float(u.used / u.total * 100) if u.total else None,
        }
    return out


def snapshot(proc: str, disks: Mapping[str, Path] | None = None) -> dict[str, Any]:
    """한 프로세스의 자원 스냅샷 한 장 — Redis 로 넘기거나 그대로 응답한다.

    Args:
        proc: 프로세스 이름표 (`api` · `engine` · `api_demo`). 여러 프로세스의 스냅샷을 한
            화면에 모을 때 구분자다.
        disks: `disk_stats` 에 넘길 `{이름: 경로}`. None 이면 디스크 항목은 빈 표.

    Returns:
        JSON 직렬화 가능한 dict — `proc`·`ts`(epoch 초)·`process`·`cgroup`·`host`·`disks`.
    """
    return {
        "proc": proc,
        "ts": time.time(),
        "process": process_stats(),
        "cgroup": {"memory": cgroup_memory(), "cpu": cgroup_cpu()},
        "host": host_stats(),
        "disks": disk_stats(disks or {}),
        "loop_lag_ms": loop_lag_stats(),
    }


LOOP_LAG_TICK_S = 1.0
"""이벤트 루프 지연 표본 주기 — 1초 타이머가 실제로 몇 ms 늦게 깨는지 잰다 (T265 눈금)."""
LOOP_LAG_WARN_MS = 500.0
"""이보다 늦으면 경고 — 그동안 이 프로세스의 모든 요청·틱이 서 있었다는 뜻이다."""


class LoopLag:
    """이벤트 루프 지연 눈금 — 순수 관측기. `observe(드리프트 ms)` 를 부르면 창을 갱신한다.

    Attributes:
        last_ms: 마지막 표본.
        max_ms: 마지막 `reset` 뒤 최대.
        samples: 마지막 `reset` 뒤 표본 수.
    """

    def __init__(self) -> None:
        """비운 눈금."""
        self.last_ms = 0.0
        self.max_ms = 0.0
        self.samples = 0

    def observe(self, drift_ms: float) -> None:
        """표본 하나 — 음수(먼저 깸)는 0 으로 본다.

        Args:
            drift_ms: 예정보다 늦게 깬 ms.
        """
        value = max(0.0, drift_ms)
        self.last_ms = value
        self.max_ms = max(self.max_ms, value)
        self.samples += 1

    def stats(self, *, reset: bool = True) -> dict[str, float | int]:
        """요약 — 스냅샷 한 장에 실린다.

        Args:
            reset: True 면 최대·표본 수를 비운다(비트 주기마다 "그동안의 최악").

        Returns:
            `{last_ms, max_ms, samples}`.
        """
        out = {
            "last_ms": round(self.last_ms, 1),
            "max_ms": round(self.max_ms, 1),
            "samples": self.samples,
        }
        if reset:
            self.max_ms = 0.0
            self.samples = 0
        return out


_loop_lag: LoopLag | None = None
_loop_lag_task: asyncio.Task[None] | None = None


def loop_lag_stats() -> dict[str, float | int] | None:
    """돌고 있는 지연 눈금의 요약.

    Returns:
        `LoopLag.stats()` — 안 띄웠으면 None (스냅샷에 그대로 실린다).
    """
    return None if _loop_lag is None else _loop_lag.stats()


def start_loop_lag(tick_s: float = LOOP_LAG_TICK_S) -> LoopLag:
    """지연 눈금을 띄운다 — 프로세스에 하나. 두 번 불러도 하나만 돈다.

    Args:
        tick_s: 표본 주기(초).

    Returns:
        눈금.

    Note:
        실행 중인 이벤트 루프 안에서 불러야 한다. 태스크 참조는 모듈이 든다 — 지역 변수로 두면
        GC 가 거둔다(`jobs.py` 와 같은 함정).
    """
    global _loop_lag, _loop_lag_task  # 프로세스 단일 눈금
    if _loop_lag is not None and _loop_lag_task is not None and not _loop_lag_task.done():
        return _loop_lag
    gauge = LoopLag()

    async def _sample() -> None:
        loop = asyncio.get_running_loop()
        while True:
            planned = loop.time() + tick_s
            await asyncio.sleep(tick_s)
            gauge.observe((loop.time() - planned) * 1000.0)

    _loop_lag = gauge
    _loop_lag_task = asyncio.get_running_loop().create_task(_sample(), name="loop_lag")
    return gauge


def warnings_for(snap: Mapping[str, Any]) -> list[str]:
    """경고선 — RAM 85% · 디스크 80% (규칙 #8: 조용히 죽지 않는다).

    Args:
        snap: `snapshot()` 이 만든 dict. 다른 프로세스에서 Redis 를 거쳐 온 것도 같은 모양이다.

    Returns:
        사람이 읽을 경고 문장 목록. 비어 있으면 정상. RAM 은 cgroup 한도가 있으면 그 대비,
        없으면 호스트 대비다 — OOM kill 을 내는 쪽이 기준이어야 한다.
    """
    out: list[str] = []
    proc = str(snap.get("proc", "?"))
    lag = snap.get("loop_lag_ms")
    if isinstance(lag, Mapping):
        lag_max = float(cast("Mapping[str, Any]", lag).get("max_ms") or 0.0)
        if lag_max >= LOOP_LAG_WARN_MS:
            out.append(
                f"{proc}: 이벤트 루프가 최대 {lag_max:.0f}ms 섰다 — 그동안 요청·틱이 전부 대기했다 "
                f"(걸음 `step_ms`·계산 오프로드 검토 · T265)"
            )
    cg = snap.get("cgroup", {}).get("memory", {})
    host = snap.get("host", {})
    pct = cg.get("percent")
    if pct is None:
        pct = host.get("mem_percent")
        scope = "호스트"
    else:
        scope = "cgroup 한도"
    if isinstance(pct, (int, float)) and pct >= RAM_WARN_PCT:
        out.append(f"{proc}: RAM {pct:.0f}% ({scope}) — {RAM_WARN_PCT:.0f}% 경고선")
    for name, d in snap.get("disks", {}).items():
        p = d.get("percent")
        if isinstance(p, (int, float)) and p >= DISK_WARN_PCT:
            out.append(f"{proc}: 디스크 {name} {p:.0f}% — {DISK_WARN_PCT:.0f}% 경고선")
    return out
