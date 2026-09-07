"""T47 — `record_stats.sh` 가 남긴 CSV 를 컨테이너별 **평균·피크**로 접는다.

```bash
uv run python scripts/dev/stats_peaks.py logs/stats/docker_stats_*.csv
```

판정 기준(T47)은 *24시간 피크 합계 ≤ 1.3 GB* 라서 피크를 먼저 낸다. 합계 피크는
"각 컨테이너 피크의 합"이 아니라 **같은 시각의 합 중 최대**다 — 둘은 다르다.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    """CSV 들을 읽어 표를 찍는다.

    Returns:
        종료 코드.
    """
    paths = [Path(arg) for arg in sys.argv[1:]]
    if not paths:
        print("사용법: stats_peaks.py <csv>...")
        return 2
    by_name: dict[str, list[float]] = defaultdict(list)
    by_ts: dict[str, float] = defaultdict(float)
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                mem = float(row["mem_mib"])
                by_name[row["container"]].append(mem)
                by_ts[row["ts"]] += mem
    if not by_ts:
        print("표본이 없다")
        return 1
    print(f"{'컨테이너':22} {'표본':>5} {'평균 MiB':>9} {'피크 MiB':>9}")
    for name, values in sorted(by_name.items(), key=lambda kv: -max(kv[1])):
        print(f"{name:22} {len(values):>5} {sum(values) / len(values):>9.1f} {max(values):>9.1f}")
    peak_ts = max(by_ts, key=by_ts.get)
    print(
        f"\n합계 평균 {sum(by_ts.values()) / len(by_ts):.0f} MiB · "
        f"합계 피크 {by_ts[peak_ts]:.0f} MiB ({peak_ts}) · 표본 {len(by_ts)}회"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
