"""장시간 작업 진행 현황 **읽기** (`make progress` · 백테스트 탭 · 진행 웹 화면).

## 왜 읽기만 여기 있고 기록기는 `scripts/` 에 남는가

`scripts/runtime/_progress.py` 가 자신을 `scripts/` 에 둔 근거는 **쓰기 경로**다:

> `common/` 에 두면 엔진·API 가 import 할 수 있게 되고, **운영 프로세스가 저장소의
> `logs/` 디렉터리에 파일을 쓰는 경로**가 생긴다.

그 근거는 기록기(`ProgressRecorder`)에만 걸린다. 이 모듈은 **읽기 전용**이라 그 경로를
만들지 않으며, 대신 API 가 진행 현황을 화면에 내보낼 수 있게 한다 — 진행을 보려고
터미널로 나가야 하는 것이 원래 이상했다.

기록기는 그대로 `scripts/` 에 남는다. 의존은 여전히 한 방향이다 (scripts → src).

## 판정은 한 곳에서만 한다

`make progress`(터미널) · 진행 웹 화면 · 백테스트 탭이 **같은 함수**를 쓴다. 각자 판정하면
"터미널은 죽었다는데 화면은 돌고 있다"가 생기고, 그때 무엇을 믿어야 할지 알 수 없다.

| 표시 | 조건 | 뜻 |
|---|---|---|
| ⏳ 진행 중 | pid 살아 있고 갱신이 최근 | 정상 |
| ⚠️ 멈춤 의심 | pid 는 살아 있는데 갱신이 끊김 | 느린 구간이거나 걸렸다 |
| 🔴 죽었다 | `running` 인데 **pid 가 없다** | 결과 없이 종료됨 — 재실행 대상 |
| ✅ 완료 / ⛔ 중단 | 작업이 스스로 남긴 종료 기록 | 끝났다 |

⚠️ **"멈춤 의심"은 정지의 증거가 아니다.** 진행 콜백을 부르지 않는 CPU 구간이 길면
멀쩡한 작업도 그렇게 보인다 (실측: v8 준비 단계가 21분간 무보고였다). 그래서 판정 이름이
`stalled`(의심)이지 `dead` 가 아니며, 둘을 가르는 것은 **pid** 다.
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from updown.common.paths import under

PROGRESS_ROOT = under("progress")
"""상태 파일 디렉터리. 기록기(`scripts/runtime/_progress.py`)와 같은 값이어야 한다."""

RECENT_FINISH_WINDOW = timedelta(hours=6)
"""끝난 작업을 계속 보여줄 기간. 이보다 오래되면 `include_old` 에서만 보인다.

완료 기록을 영원히 띄우면 화면이 과거로 가득 차 **지금 도는 것**이 묻힌다. 반대로 즉시
지우면 "방금 끝났나 죽었나"를 확인할 창이 사라진다.
"""

DEFAULT_STALL_SECONDS = 120.0
"""`stall_after_seconds` 가 없는 낡은 스냅샷의 기본 임계값."""

ACTIVE_VERDICTS = frozenset({"running", "stalled", "dead", "unknown"})
"""아직 끝나지 않은 판정들. 죽은 것·미상도 포함한다 — 조치가 필요하기 때문이다."""

_ORDER = {"dead": 0, "stalled": 1, "running": 2, "unknown": 3, "aborted": 4, "done": 5}
"""정렬 우선순위 — **조치가 필요한 것이 위**로 온다."""


def pid_namespace() -> int | None:
    """지금 프로세스의 PID 네임스페이스 식별자.

    Returns:
        `/proc/self/ns/pid` 의 inode. 못 읽으면 None (리눅스가 아니거나 procfs 없음).

    Note:
        이 값이 **다르면 상대의 pid 를 볼 수 없다.** 기록기가 남기고 판정기가 비교해서
        "pid 검사가 뜻이 있는가"를 먼저 정한다 — 안 그러면 다른 네임스페이스의 살아 있는
        작업을 전부 `dead` 로 보고하게 된다 (`classify` 참조).
    """
    try:
        return Path("/proc/self/ns/pid").stat().st_ino
    except OSError:
        return None


def pid_alive(pid: int) -> bool:
    """프로세스가 살아 있는가.

    Args:
        pid: 확인할 프로세스 ID.

    Returns:
        살아 있으면 True.

    Note:
        `os.kill(pid, 0)` 은 신호를 보내지 않고 존재만 확인한다. `PermissionError` 는
        **다른 사용자의 프로세스**라는 뜻이므로 살아 있는 것으로 친다 — 없는 것으로 치면
        남의 프로세스를 "죽었다"고 보고하게 된다.

        ⚠️ pid 재사용 가능성은 남는다. 완벽한 판정이 아니라 **오판을 크게 줄이는 신호**로
        쓴다 — 갱신 시각과 함께 보면 실질적으로 충분하다.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class JobStatus:
    """작업 하나의 판정 결과.

    Attributes:
        job: 작업 이름.
        state: 기록기가 남긴 상태 (`running` / `done` / `aborted`).
        verdict: 이 모듈의 판정 (`running` / `stalled` / `dead` / `done` / `aborted` /
            `unknown`).
        done: 처리한 작업량.
        total: 전체 작업량. 0 이면 미상.
        unit: 단위 표기.
        elapsed_seconds: 경과 시간.
        eta_seconds: 남은 예상 시간. 산출 불가면 None.
        rate_per_second: 처리 속도.
        since_update_seconds: 마지막 갱신 이후 경과.
        note: 종료 사유 등.
        updated_at: 마지막 갱신 시각.
    """

    job: str
    state: str
    verdict: str
    done: int
    total: int
    unit: str
    elapsed_seconds: float
    eta_seconds: float | None
    rate_per_second: float
    since_update_seconds: float
    note: str
    updated_at: datetime

    @property
    def ratio(self) -> float | None:
        """진행 비율. 전체를 모르면 None."""
        if self.total <= 0:
            return None
        return min(1.0, self.done / self.total)

    @property
    def is_active(self) -> bool:
        """아직 끝나지 않은 작업인가 (죽은 것·미상도 포함 — 조치가 필요하다)."""
        return self.verdict in ACTIVE_VERDICTS


def _pid_is_checkable(snapshot: dict[str, Any]) -> bool:
    """이 스냅샷의 pid 를 우리가 확인할 수 있는가.

    Args:
        snapshot: 기록기가 남긴 스냅샷.

    Returns:
        같은 PID 네임스페이스에서 쓰인 것이 **확실하면** True.

    Note:
        네임스페이스를 안 남긴 낡은 스냅샷은 **False** 로 본다. 모르면 확신하지
        않는다 — 확신하는 쪽의 대가가 "멀쩡히 도는 작업을 죽었다고 보고"이기 때문이다.
    """
    recorded = snapshot.get("pid_ns")
    return recorded is not None and recorded == pid_namespace()


def classify(snapshot: dict[str, Any], now: datetime) -> JobStatus:
    """`.json` 스냅샷을 판정한다.

    Args:
        snapshot: 기록기가 남긴 스냅샷.
        now: 판정 기준 시각.

    Returns:
        판정 결과.

    Note:
        **`running` 인데 pid 가 없으면 `dead` 다.** 이것이 이 모듈의 존재 이유다 —
        작업이 스스로 종료를 남기지 못하고 죽으면(OOM·kill·정전) `.status` 는 마지막
        진행률에서 멈춘 채 남고, 그것만 보면 아직 도는 것처럼 보인다.

        🔴 **단, pid 가 안 보이는 이유가 둘이다.**

            ① 정말 죽었다
            ② 우리가 그 pid 를 볼 수 없다 — 읽는 쪽이 다른 PID 네임스페이스에 있다

        ②는 가정이 아니라 실제로 겪었다. 매트릭스는 WSL 호스트에서 돌고 API 는 Docker
        Desktop VM 안의 컨테이너에서 돈다. 6워커가 멀쩡히 도는 v13 을 화면이 전부
        **"🔴 죽었다 · 재실행 대상"** 으로 표시했다. (`pid: host` 로도 안 된다 —
        공유되는 것은 Docker VM 의 네임스페이스이지 WSL 의 것이 아니다.)

        아무것도 안 보이는 것보다 나쁘다. 그 화면을 믿으면 멀쩡한 7시간짜리 작업을
        죽이고 다시 돌린다.

        가르는 근거는 **갱신 시각**이다 — **죽은 프로세스는 파일을 쓰지 않는다.**
        pid 가 안 보이는데 방금 갱신됐다면 ②다. 갱신도 끊겼으면 ①로 본다.
    """
    state = str(snapshot.get("state", "running"))
    updated_at = datetime.fromisoformat(str(snapshot["updated_at"]))
    since = (now - updated_at).total_seconds()
    stalled = since > float(snapshot.get("stall_after_seconds", DEFAULT_STALL_SECONDS))

    if state in {"done", "aborted"}:
        verdict = state
    elif pid_alive(int(snapshot.get("pid", 0))):
        verdict = "stalled" if stalled else "running"
    elif _pid_is_checkable(snapshot):
        # 같은 네임스페이스인데 pid 가 없다 = 정말 죽었다. 즉시 그렇게 말한다.
        verdict = "dead"
    else:
        # 상대의 pid 를 볼 수 없다. 갱신이 살아 있으면 **안 보이는** 것이지 죽은 것이
        # 아니다 — 죽은 프로세스는 파일을 쓰지 않는다.
        verdict = "dead" if stalled else "running"

    eta = snapshot.get("eta_seconds")
    return JobStatus(
        job=str(snapshot.get("job", "?")),
        state=state,
        verdict=verdict,
        done=int(snapshot.get("done", 0)),
        total=int(snapshot.get("total", 0)),
        unit=str(snapshot.get("unit", "")),
        elapsed_seconds=float(snapshot.get("elapsed_seconds", 0.0)),
        eta_seconds=None if eta is None else float(eta),
        rate_per_second=float(snapshot.get("rate_per_second", 0.0)),
        since_update_seconds=since,
        note=str(snapshot.get("note", "")),
        updated_at=updated_at,
    )


def _from_status_only(path: Path, now: datetime) -> JobStatus:
    """`.json` 없이 `.status` 만 있는 작업 — 판정을 **미상**으로 남긴다.

    Args:
        path: `.status` 경로.
        now: 판정 기준 시각.

    Returns:
        `verdict="unknown"` 인 결과. 파일 수정 시각만 신뢰한다.

    Note:
        pid 를 모르므로 죽음/멈춤을 가를 수 없다. **모르는 것을 아는 척하지 않는다** —
        `running` 으로 표시하면 죽은 작업을 살아 있다고 보고하게 되고, `dead` 로 표시하면
        멀쩡한 작업을 죽었다고 보고하게 된다. 둘 다 이 도구의 목적을 배신한다.
    """
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).astimezone()
    try:
        head = path.read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        head = ""
    return JobStatus(
        job=path.stem,
        state="unknown",
        verdict="unknown",
        done=0,
        total=0,
        unit="",
        elapsed_seconds=0.0,
        eta_seconds=None,
        rate_per_second=0.0,
        since_update_seconds=(now - updated_at).total_seconds(),
        note=head,
        updated_at=updated_at,
    )


def load_all(root: Path, now: datetime) -> list[JobStatus]:
    """디렉터리의 모든 작업을 읽어 판정한다.

    Args:
        root: 상태 파일 디렉터리.
        now: 판정 기준 시각.

    Returns:
        판정 결과들. **조치가 필요한 것이 먼저**, 그다음 최근 갱신 순이다.

    Note:
        깨진 `.json` 은 건너뛰되 **조용히 넘기지 않는다** — 표준오류로 알린다. 상태 파일이
        깨졌다는 것은 기록기 쪽 문제이고, 그것을 숨기면 "작업이 사라졌다"로 보인다
        (절대 규칙 #8).
    """
    statuses: list[JobStatus] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"  ⚠️ {path.name} 을 읽을 수 없다: {error}", file=sys.stderr)
            continue
        if not isinstance(raw, dict):
            print(f"  ⚠️ {path.name} 형식이 예상 밖이다", file=sys.stderr)
            continue
        seen.add(path.stem)
        statuses.append(classify(cast(dict[str, Any], raw), now))

    # `.status` 만 있고 `.json` 이 없는 작업도 **반드시 보여준다.**
    #
    # 사이드카가 없다는 이유로 목록에서 빼면 "작업이 없다"로 보이는데, 그것이 이 모듈이
    # 없애려는 오판 그 자체다. 사이드카 도입 전에 띄운 작업이 실제로 이 상태였다.
    for path in sorted(root.glob("*.status")):
        if path.stem in seen:
            continue
        statuses.append(_from_status_only(path, now))

    statuses.sort(key=lambda item: (_ORDER.get(item.verdict, 9), -item.updated_at.timestamp()))
    return statuses


def visible(root: Path, now: datetime, *, include_old: bool = False) -> list[JobStatus]:
    """화면에 낼 작업들 — 진행 중 + 최근 끝난 것.

    Args:
        root: 상태 파일 디렉터리.
        now: 판정 기준 시각.
        include_old: 오래 전에 끝난 것까지 포함할지.

    Returns:
        판정 결과들. 디렉터리가 없으면 빈 목록.
    """
    if not root.exists():
        return []
    statuses = load_all(root, now)
    if include_old:
        return statuses
    return [
        item for item in statuses if item.is_active or now - item.updated_at <= RECENT_FINISH_WINDOW
    ]


def payload(status: JobStatus) -> dict[str, Any]:
    """작업 하나를 기계용 dict 로 만든다.

    Args:
        status: 판정 결과.

    Returns:
        JSON 직렬화 가능한 dict.

    Note:
        터미널의 `--json`, 진행 웹 화면, 백테스트 탭이 **같은 함수**를 쓴다. 각자 dict 를
        만들면 필드가 갈라지고, 그때 화면만 조용히 옛 필드를 읽게 된다.
    """
    return {
        "job": status.job,
        "verdict": status.verdict,
        "done": status.done,
        "total": status.total,
        "unit": status.unit,
        "ratio": status.ratio,
        "elapsed_seconds": status.elapsed_seconds,
        "eta_seconds": status.eta_seconds,
        "rate_per_second": status.rate_per_second,
        "since_update_seconds": status.since_update_seconds,
        "note": status.note,
        "updated_at": status.updated_at.isoformat(),
        "is_active": status.is_active,
    }


SIDECARS = (".status", ".log", ".json")
"""진행률 기록 한 건을 이루는 파일들 — `_progress.py` 기록기가 만드는 세 개다."""


def forget(root: Path, job: str) -> int:
    """작업 하나의 **진행률 기록**을 지운다.

    Args:
        root: 진행률 디렉터리.
        job: 작업 이름.

    Returns:
        지운 파일 수. 0 이면 기록이 없었다는 뜻이다.

    Note:
        🔴 **측정 결과가 아니라 관측 기록**이다. `logs/results/` 의 리포트는 건드리지
        않는다 — 둘을 한 함수로 합치면 "카드만 치우려다 결과를 날리는" 사고가 난다.

        ⛔ **도는지 여부를 여기서 보지 않는다.** 그 판단은 `visible`/`classify` 가 하고
        호출부가 거부한다 — 지우기 함수가 판정까지 겸하면 테스트에서 시간을 물려야 하고,
        무엇보다 "왜 안 지워졌나"의 답이 두 곳으로 갈린다.

        이름을 그대로 경로로 쓰지 않는다. 구분자를 치환한 뒤 **해석한 경로가 root 안에
        있는지** 확인한다 — `../../.env` 같은 이름이 들어와도 여기서 멈춘다.
    """
    safe = job.replace("/", "_").replace("\\", "_").replace(" ", "_")
    removed = 0
    for suffix in SIDECARS:
        path = root / f"{safe}{suffix}"
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return 0
        if path.exists():
            path.unlink()
            removed += 1
    return removed
