"""T759 — `load_playbooks()` 한 번이 몇 ms 인지 잰다 (로컬 · 연구 PC 에서 `uv run python`).

왜: 11-02 실계좌 복원이 루프를 43 ~ 50 초 막았고, 그 멤버 루프가 이름마다 `load_playbooks()` 를
다시 부른다는 가설의 크기를 재려는 것이다. DB 선언은 끈다(`UPDOWN_PLAYBOOK_DB=0`) — 파일 몫만.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("UPDOWN_PLAYBOOK_DB", "0")

from updown.analysis import plugins
from updown.analysis.playbook.select import _load_file, load_playbooks


def main() -> None:
    """파일 목록 · 파일별 파싱 ms · `load_playbooks()` 3 회 ms 를 찍는다."""
    started = time.perf_counter()
    files = plugins.playbook_files()
    discovery_ms = (time.perf_counter() - started) * 1000
    print("files", len(files), [str(f)[-50:] for f in files], f"entry_points {discovery_ms:.0f} ms")
    for target in files:
        started = time.perf_counter()
        books = _load_file(target)
        elapsed_ms = (time.perf_counter() - started) * 1000
        size_kb = target.stat().st_size // 1024
        print(f"  {str(target)[-45:]}: {len(books)} books {elapsed_ms:.0f} ms  size {size_kb} KB")
    for index in range(3):
        started = time.perf_counter()
        books = load_playbooks()
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"load_playbooks #{index}: {len(books)} books {elapsed_ms:.0f} ms")


if __name__ == "__main__":
    main()
