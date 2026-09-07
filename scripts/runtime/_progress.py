"""장시간 측정 작업의 진행률 기록기 (세션 인계 §5-2, §6-2).

## 왜 필요한가 — "느린 것"과 "죽은 것"을 구분할 수 없었다

지난 세션에서 정상 완료한 측정을 **"결과 없이 죽었다"고 판단해 같은 측정을 다시 띄웠다.**
원인은 셋이다:

| # | 원인 | 증상 |
|---|---|---|
| 1 | stdout 블록 버퍼링 | 파일로 리다이렉트하면 진행 출력이 버퍼에 갇혀 17분간 아무것도 안 보인다 |
| 2 | 로그가 `/tmp` | 저장소 밖이라 IDE 에서 안 보이고 WSL 재시작 때 사라진다 |
| 3 | 진행률·ETA 없음 | 봉 번호만 찍혀 **몇 %인지, 언제 끝나는지** 모른다 |

이 모듈은 셋을 한 번에 해결한다 — `logs/progress/<job>.status` 에 **퍼센트·ETA·속도**를
원자적으로 덮어쓰고, 같은 줄을 stdout 에 `flush=True` 로도 낸다.

## 왜 `src/updown/` 이 아니라 `scripts/` 인가

이것은 **측정 작업의 관심사**이지 런타임 도메인이 아니다. `common/` 에 두면 엔진·API 가
import 할 수 있게 되고, 운영 프로세스가 저장소의 `logs/` 디렉터리에 파일을 쓰는 경로가
생긴다. 진행률은 사람이 보려고 만드는 것이므로 사람이 돌리는 쪽에 둔다.

`analysis.evaluation.scan.collect()` 는 이 모듈을 모른다 — `on_progress` 콜백만 받는다.
그래서 의존은 한 방향(scripts → src)으로만 흐른다.

## 읽는 방법

```bash
make progress                 # 한 번 보기
watch -n 5 make progress      # 실시간 감시
```

VS Code 에서 `logs/progress/<job>.status` 를 **탭으로 열어 두면** 파일이 바뀔 때마다
자동 갱신된다. 별도 도구 없이 되는 가장 싼 방법이다.

## 장시간 작업 실행 규약

```bash
setsid nohup uv run python -u scripts/<name>.py > logs/<name>.log 2>&1 < /dev/null &
```

`setsid` 로 세션을 분리해야 하네스 10분 타임아웃과 무관해지고, `-u` 가 원인 1을 막는다.
(`-u` 없이도 이 기록기의 `.status` 는 보이지만, 로그 파일까지 실시간이 되는 편이 낫다.)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

PROGRESS_ROOT = Path("logs/progress")
"""상태 파일 디렉터리. `logs/` 는 `.gitignore:36` 에 있어 커밋을 오염시키지 않는다."""

BAR_WIDTH = 20
"""진행 막대 칸 수."""

DEFAULT_MIN_INTERVAL = 1.0
"""갱신 최소 간격(초). 매 호출마다 파일을 쓰면 그 자체가 측정을 느리게 한다."""

MIN_STALL_SECONDS = 120.0
"""'멈춤 의심' 판정의 하한. 이보다 자주 갱신되는 작업도 2분은 기다려 준다."""

MIN_ETA_ELAPSED = 5.0
"""ETA 를 내기 시작하는 최소 경과 시간(초).

첫 갱신은 시작 직후라 "1라운드를 0.18초에 처리"처럼 잡히고, 그 속도로 나눈 ETA 는
**실제보다 수십 배 짧게** 나온다(24시간 작업에 4분이라고 적힌다). 거짓 ETA 는 없는
것보다 나쁘다 — 그것을 보고 기다리는 판단을 하게 된다. 표본이 쌓일 때까지 '계산 중'이다.
"""

STALL_GAP_FACTOR = 4.0
"""관측된 최대 갱신 간격의 몇 배까지 정상으로 볼지.

작업마다 갱신 주기가 다르다 — 비용 샘플링은 10초, 봉 스캔은 수십 초다. 고정 임계값을 두면
느린 작업이 계속 '멈춤'으로 오인되거나, 빠른 작업의 정지를 늦게 발견한다. **자기 이력에서
임계값을 만드는 것**이 두 경우 모두를 피하는 방법이다.
"""


def _clock(seconds: float) -> str:
    """초를 `MM:SS` 또는 `H:MM:SS` 로 만든다."""
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _pid_namespace() -> int | None:
    """지금 프로세스의 PID 네임스페이스 식별자.

    Returns:
        `/proc/self/ns/pid` 의 inode. 못 읽으면 None.

    Note:
        읽는 쪽(`reporting/progress`)이 자기 값과 비교해 **pid 검사가 가능한지**를
        먼저 정한다. 값이 다르면 pid 가 안 보이는 것이 정상이므로 죽었다고 하지 않는다.
    """
    try:
        return Path("/proc/self/ns/pid").stat().st_ino
    except OSError:
        return None


class ProgressRecorder:
    """작업 하나의 진행률을 `logs/progress/<job>.*` 에 기록한다.

    파일 셋을 만든다:

    | 파일 | 용도 | 읽는 쪽 |
    |---|---|---|
    | `.status` | 사람이 읽는 **현재 상태** 두 줄 (계속 덮어쓰기) | IDE 탭 · `cat` |
    | `.log` | 갱신 **이력** (append) | 속도가 떨어졌는지 볼 때 |
    | `.json` | 기계가 읽는 상태 — **pid · 상태 · 시각** | `progress_report.py` |

    Note:
        `.json` 이 있어야 **"죽은 것"과 "느린 것"을 구분**할 수 있다. `.status` 만으로는
        마지막 줄이 언제 쓰였는지·그 프로세스가 아직 사는지 알 수 없어, 결국 지난 세션의
        오판(정상 완료한 측정을 죽은 줄 알고 다시 띄운 일)을 다시 하게 된다.
        사람이 읽는 텍스트를 파싱하지 않고 별도 파일을 두는 이유는, 표시 형식을 바꿀 때
        판정 로직이 조용히 깨지는 것을 막기 위해서다.

        덮어쓰기는 임시 파일 + `Path.replace`(원자적 rename) 다. 직접 열어 쓰면 읽는 쪽이
        **잘린 줄**을 볼 수 있고, 그러면 진행률을 보려다 오히려 헷갈린다.
    """

    def __init__(
        self,
        job: str,
        total: int,
        *,
        unit: str = "봉",
        root: Path | None = None,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        echo: bool = True,
    ) -> None:
        """기록기를 만든다.

        Args:
            job: 작업 이름. 파일명이 되므로 `/` 는 `_` 로 바뀐다.
            total: 전체 작업량. 0 이면 퍼센트·ETA 없이 개수만 낸다.
            unit: 작업량의 단위 표기 (`봉` / `샘플` …).
            root: 상태 파일 디렉터리. None 이면 `PROGRESS_ROOT`.
            min_interval: 갱신 최소 간격(초).
            echo: 같은 줄을 stdout 에도 낼지. `flush=True` 로 낸다.
        """
        self.job = job
        self.total = max(0, total)
        self.unit = unit
        #: 하위 진행 표기 (예: `"창 3/32"`). 호출부가 자유롭게 바꾼다.
        #:
        #: 단위 하나가 오래 걸리는 작업에 필요하다 — 완료 수만 찍으면 그 사이가
        #: 무신호라 '멈춤 의심'과 구분되지 않는다.
        self.note = ""
        self._min_interval = min_interval
        self._echo = echo
        self._root = root or PROGRESS_ROOT
        self._root.mkdir(parents=True, exist_ok=True)
        safe = job.replace("/", "_").replace(" ", "_")
        self._status_path = self._root / f"{safe}.status"
        self._log_path = self._root / f"{safe}.log"
        self._state_path = self._root / f"{safe}.json"
        self._started = time.monotonic()
        self._started_at = datetime.now().astimezone()
        self._last_write = 0.0
        self._done = 0
        self._max_gap = 0.0
        self._state = "running"
        self._note = ""
        self._write_state(self._snapshot(self._started))

    @property
    def status_path(self) -> Path:
        """현재 상태 파일 경로."""
        return self._status_path

    @property
    def state_path(self) -> Path:
        """기계가 읽는 상태 파일 경로."""
        return self._state_path

    def update(self, done: int, *, force: bool = False) -> None:
        """진행량을 갱신한다.

        Args:
            done: 지금까지 처리한 작업량.
            force: 최소 간격을 무시하고 즉시 쓸지.

        Note:
            간격 제한이 없으면 봉마다 파일을 쓰게 되고, 그 I/O 가 측정 자체를 느리게 한다
            — 진행률을 보려고 측정을 늦추는 것은 본말전도다.
        """
        self._done = done
        now = time.monotonic()
        if not force and now - self._last_write < self._min_interval:
            return
        if self._last_write:
            # 관측된 갱신 간격에서 '멈춤' 임계값을 만든다 (모듈 상수 STALL_GAP_FACTOR).
            self._max_gap = max(self._max_gap, now - self._last_write)
        self._last_write = now
        self._write(self._render(now))
        self._write_state(self._snapshot(now))

    def restart_clock(self) -> None:
        """경과·속도 계산을 **지금부터** 다시 센다.

        Note:
            🔴 준비 단계와 본 작업의 시계를 나누기 위한 것이다. 준비(캔들 로드 · 색인
            구축)를 진행률에 드러내면서 시계를 그대로 두면, 본 작업이 시작된 뒤에도
            **준비 시간이 분모에 남아** 속도가 실제보다 훨씬 느리게 보인다.

            실측: BTC 15m 이 `800봉 / 경과 16:45 = 0.80봉/s` 로 찍혔는데, 그중 14분이
            색인 구축이었다. 실제 스캔 속도는 약 6.7봉/s 다. 그 상태로는 ETA 가
            8배 부풀고 "멈춤 의심" 판정까지 흔들린다.

            `total` 을 채우는 시점(본 작업 시작)에 함께 부른다.
        """
        self._started = time.monotonic()
        self._last_write = 0.0
        self._max_gap = 0.0

    def finish(self, note: str = "완료") -> None:
        """작업 종료를 기록한다.

        Args:
            note: 종료 사유 (`완료` / `중단: ...`).

        Note:
            종료 줄을 남기지 않으면 `.status` 가 마지막 갱신 시점에 멈춘 채로 남아,
            나중에 보는 사람이 **아직 도는 중**이라고 오해한다.
        """
        now = time.monotonic()
        elapsed = now - self._started
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._state = "done" if note.startswith("완료") else "aborted"
        self._note = note
        line = (
            f"{self.job}  {note}  {self._done:,} / {self.total:,}{self.unit}\n"
            f"  총 소요 {_clock(elapsed)} · 종료 {stamp}\n"
        )
        self._write(line)
        self._write_state(self._snapshot(now))

    def __enter__(self) -> Self:
        """컨텍스트 진입 — **즉시 상태를 쓴다.**

        Returns:
            자기 자신.

        Note:
            🔴 진입 시점에 쓰지 않으면 **살아 있는 작업이 죽은 것처럼 보인다** (실측).

            같은 이름으로 전에 돌다 죽은 작업이 있으면 상태 파일에 **옛 PID** 가 남아
            있고, 판정기는 그 PID 가 없으니 "죽었다"로 읽는다. 새 프로세스가 첫 진행
            보고(`progress_every` 봉마다)를 낼 때까지 그 오판이 유지되는데, 느린
            작업에서는 그게 몇 분이다 — 실제로 BTC 15m(6.6봉/s)에서 5분이었다.

            시작하자마자 자기 PID 로 파일을 덮으면 그 창이 사라진다. 이 도구의 목적이
            "죽은 것과 느린 것을 구분"이므로, **막 시작한 것**도 구분되어야 한다.
        """
        self.update(0, force=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """컨텍스트 이탈 — 예외로 끝났으면 그 사실을 상태에 남긴다."""
        self.finish("완료" if exc_type is None else f"중단: {exc_type.__name__}")

    # ------------------------------------------------------------------

    def _render(self, now: float) -> str:
        """상태 두 줄을 만든다."""
        elapsed = now - self._started
        rate = self._done / elapsed if elapsed > 0 and self._done else 0.0
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")

        if self.total:
            ratio = min(1.0, self._done / self.total)
            filled = int(ratio * BAR_WIDTH)
            arrow = ">" if 0 < filled < BAR_WIDTH else ""
            bar = ("=" * max(0, filled - len(arrow)) + arrow).ljust(BAR_WIDTH)
            remaining = self._eta(elapsed, rate)
            head = (
                f"{self.job}  {ratio * 100:5.1f}%  [{bar}]  "
                f"{self._done:,} / {self.total:,}{self.unit}"
            )
            eta = f" · 남은 ~{_clock(remaining)}" if remaining is not None else " · 남은 ~계산 중"
        else:
            head = f"{self.job}  {self._done:,}{self.unit}"
            eta = ""

        # 하위 진행(예: 백필의 창 3/32)을 덧붙인다. 완료 수를 건드리지 않으므로
        # 퍼센트는 그대로이고, **긴 단위 안에서도 살아 있다는 신호**가 남는다.
        detail = f" · {self.note}" if self.note else ""
        return (
            f"{head}\n  경과 {_clock(elapsed)}{eta} · "
            f"속도 {rate:.1f}{self.unit}/s{detail} · 갱신 {stamp}\n"
        )

    def _eta(self, elapsed: float, rate: float) -> float | None:
        """남은 예상 시간. 표본이 모자라면 **None** — 거짓 ETA 를 내지 않는다.

        Args:
            elapsed: 시작 이후 경과(초).
            rate: 지금까지의 처리 속도.

        Returns:
            남은 초. 아직 낼 수 없으면 None.
        """
        if rate <= 0 or elapsed < MIN_ETA_ELAPSED or not self.total:
            return None
        return (self.total - self._done) / rate

    def _snapshot(self, now: float) -> dict[str, object]:
        """기계가 읽는 상태 스냅샷.

        Args:
            now: `time.monotonic()` 기준 현재 시각.

        Returns:
            `.json` 에 쓸 dict.

        Note:
            **`pid` 와 `pid_ns` 가 핵심 필드다.** 이것이 있어야 읽는 쪽이 "프로세스가 아직 사는가"를
            확인해 *죽음*과 *느림*을 가른다. `stall_after_seconds` 는 자기 이력에서 만든
            임계값이라 갱신 주기가 다른 작업들을 같은 잣대로 보지 않는다.
        """
        elapsed = now - self._started
        rate = self._done / elapsed if elapsed > 0 and self._done else 0.0
        remaining = self._eta(elapsed, rate) if self.total else None
        return {
            "job": self.job,
            "pid": os.getpid(),
            # 🔴 pid 만으로는 부족하다. 읽는 쪽이 **다른 PID 네임스페이스**에
            #    있으면 이 pid 를 볼 수 없고, 그러면 멀쩡히 도는 작업이 전부
            #    `dead` 로 보고된다 (컨테이너 API 가 호스트 매트릭스를 볼 때
            #    실제로 그랬다). 네임스페이스를 함께 남겨 읽는 쪽이 **pid 검사가
            #    뜻이 있는지부터** 판단하게 한다 (`reporting/progress.classify`).
            "pid_ns": _pid_namespace(),
            "state": self._state,
            "note": self._note,
            "done": self._done,
            "total": self.total,
            "unit": self.unit,
            "started_at": self._started_at.isoformat(),
            "updated_at": datetime.now().astimezone().isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "rate_per_second": round(rate, 4),
            "eta_seconds": None if remaining is None else round(remaining, 1),
            "stall_after_seconds": max(MIN_STALL_SECONDS, self._max_gap * STALL_GAP_FACTOR),
        }

    def _write_state(self, snapshot: dict[str, object]) -> None:
        """`.json` 을 원자적으로 덮어쓴다."""
        temp = self._state_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self._state_path)

    def _write(self, line: str) -> None:
        """상태 파일을 원자적으로 덮어쓰고 이력에 append 한다."""
        temp = self._status_path.with_suffix(".status.tmp")
        temp.write_text(line, encoding="utf-8")
        temp.replace(self._status_path)
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(line)
        if self._echo:
            print(line.rstrip("\n"), flush=True)
