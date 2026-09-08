"""모든 장시간 작업의 진행 현황을 한 화면에 모은다 (`make progress`).

## 왜 `cat *.status` 로는 부족한가

상태 파일을 그냥 출력하면 **마지막으로 쓰인 줄**만 보인다. 그 줄이 3초 전 것인지 3시간 전
것인지, 그 프로세스가 아직 사는지 알 수 없다. 지난 세션의 오판이 정확히 그것이었다 —
정상 완료한 측정을 "결과 없이 죽었다"고 판단해 같은 측정을 다시 띄웠다.

이 리포터는 `.json` 사이드카의 **pid 와 갱신 시각**으로 네 상태를 가른다:

| 표시 | 조건 | 뜻 |
|---|---|---|
| ⏳ 진행 중 | pid 살아 있고 갱신이 최근 | 정상 |
| ⚠️ 멈춤 의심 | pid 는 살아 있는데 갱신이 끊김 | 느린 구간이거나 걸렸다 |
| 🔴 죽었다 | `running` 인데 **pid 가 없다** | 결과 없이 종료됨 — 재실행 대상 |
| ✅ 완료 / ⛔ 중단 | 작업이 스스로 남긴 종료 기록 | 끝났다 |

**'멈춤' 임계값은 작업마다 다르다.** 비용 샘플링은 10초마다, 봉 스캔은 수십 초마다
갱신한다. 고정 임계값이면 느린 작업이 계속 오탐되므로, 기록기가 **자기 갱신 이력에서**
임계값을 만들어 `.json` 에 넣는다 (`stall_after_seconds`).

## 무엇이 여기 나타나는가

`ProgressRecorder` 를 쓰는 모든 작업이다:

- `scripts/runtime/measure_costs.py` — 비용 실측 (호가 샘플링)
- `scripts/research/timeframe_viability.py` — 타임프레임 성립성 (봉 스캔, 모드당 ~72분)
- `scripts/research/follow_through_report.py` — 후속 이행률 (봉 스캔)
- `scripts/runtime/backfill_cli.py` — 캔들 백필
- **pytest** — `tests/conftest.py` 가 테스트 진행을 같은 형식으로 남긴다

## 사용법

```bash
make progress          # 진행 중 + 최근 끝난 것
make progress-watch    # 2초마다 갱신
uv run python scripts/dev/progress_report.py --all      # 오래된 완료까지 전부
uv run python scripts/dev/progress_report.py --json     # 기계용
```
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# 재정리(2026-09-06): scripts/ 하위 폴더끼리 import — 자기 폴더 · scripts/ · runtime/ · research/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _progress import BAR_WIDTH, PROGRESS_ROOT, _clock

from updown.orchestration.reporting.progress import (
    RECENT_FINISH_WINDOW,
    JobStatus,
    classify,
    load_all,
    payload,
    visible,
)

# 판정 로직은 `orchestration/reporting/progress.py` 에 있다. 여기 남는 것은 **터미널
# 렌더링**뿐이다 — API 가 `scripts/` 를 import 할 수 없어서 옮겼고, 각자 판정하면
# "터미널은 죽었다는데 화면은 돌고 있다"가 생긴다.
__all__ = [
    "RECENT_FINISH_WINDOW",
    "JobStatus",
    "classify",
    "load_all",
    "payload",
    "visible",
]


_MARKS = {
    "running": "⏳ 진행 중",
    "stalled": "⚠️ 멈춤 의심",
    "dead": "🔴 죽었다",
    "done": "✅ 완료",
    "aborted": "⛔ 중단",
    "unknown": "❔ 상태 미상",
}


def _bar(ratio: float | None) -> str:
    """진행 막대."""
    if ratio is None:
        return " " * BAR_WIDTH
    filled = int(ratio * BAR_WIDTH)
    arrow = ">" if 0 < filled < BAR_WIDTH else ""
    return ("=" * max(0, filled - len(arrow)) + arrow).ljust(BAR_WIDTH)


def render(statuses: list[JobStatus]) -> list[str]:
    """사람이 읽는 줄들을 만든다.

    Args:
        statuses: 판정 결과들.

    Returns:
        출력할 줄들.
    """
    if not statuses:
        return ["진행 중인 작업 없음"]

    lines: list[str] = []
    active = [item for item in statuses if item.is_active]
    lines.append(
        f"작업 {len(statuses)}건 — 진행 중 {sum(1 for i in statuses if i.verdict == 'running')} · "
        f"멈춤 의심 {sum(1 for i in statuses if i.verdict == 'stalled')} · "
        f"죽음 {sum(1 for i in statuses if i.verdict == 'dead')} · "
        f"끝남 {len(statuses) - len(active)}"
    )
    lines.append("")

    for item in statuses:
        mark = _MARKS.get(item.verdict, item.verdict)
        ratio = item.ratio
        percent = f"{ratio * 100:5.1f}%" if ratio is not None else "  ?  "
        total = f"{item.total:,}" if item.total else "?"
        lines.append(
            f"{mark:<12} {item.job:<24} {percent}  [{_bar(ratio)}]  "
            f"{item.done:,} / {total}{item.unit}"
        )

        detail = f"    경과 {_clock(item.elapsed_seconds)}"
        if item.verdict in {"running", "stalled"} and item.eta_seconds is not None:
            detail += f" · 남은 ~{_clock(item.eta_seconds)}"
        if item.rate_per_second:
            detail += f" · {item.rate_per_second:.2f}{item.unit}/s"
        detail += f" · 갱신 {item.updated_at.strftime('%H:%M:%S')}"
        if item.verdict in {"running", "stalled", "dead"}:
            detail += f" ({_clock(item.since_update_seconds)} 전)"
        lines.append(detail)

        if item.verdict == "dead":
            lines.append("    🔴 프로세스가 없다 — 결과 없이 종료됐다. 로그를 확인하고 다시 띄운다")
        elif item.verdict == "stalled":
            lines.append("    ⚠️ 프로세스는 살아 있는데 갱신이 끊겼다 — 느린 구간이거나 걸렸다")
        elif item.verdict == "unknown":
            lines.append("    ❔ .json 사이드카가 없어 생사를 알 수 없다 (구형 기록)")
            if item.note:
                lines.append(f"       마지막 줄: {item.note}")
        elif item.note and item.verdict in {"done", "aborted"}:
            lines.append(f"    {item.note}")
        lines.append("")
    return lines


def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드. **죽었거나 멈춘 작업이 있으면 1** — 스크립트에서 조건 분기가 된다.
    """
    parser = argparse.ArgumentParser(description="장시간 작업 진행 현황 (make progress)")
    parser.add_argument("--root", type=Path, default=PROGRESS_ROOT)
    parser.add_argument("--all", action="store_true", help="오래 전에 끝난 작업까지 보여준다")
    parser.add_argument("--json", action="store_true", help="기계용 JSON 출력")
    args = parser.parse_args()

    root: Path = args.root
    now = datetime.now(UTC).astimezone()
    statuses = visible(root, now, include_old=bool(args.all))

    if args.json:
        print(json.dumps([payload(item) for item in statuses], ensure_ascii=False, indent=2))
    else:
        print("\n".join(render(statuses)))

    return 1 if any(item.verdict in {"dead", "stalled"} for item in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
