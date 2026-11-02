"""T759 ④' — 선언 파일 파싱 캐시 (2026-11-02 실계좌 복원 43 ~ 50 초 막힘의 뿌리).

막아야 하는 실패:
1. 🔴 같은 파일을 부를 때마다 YAML 전체를 다시 파싱하는 것(멤버 39 x 이름 3 ~ 4 = 루프 막힘).
2. 🔴 파일이 바뀌었는데 옛 선언을 돌려주는 것 — 캐시는 **같은 입력 → 같은 출력**이어야 한다.
3. ⚠️ 파싱 실패를 캐시해 다음 호출까지 깨진 상태로 두는 것.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from updown.analysis.playbook import select
from updown.analysis.playbook.select import (
    PlaybookConfigError,
    forget_file_cache,
    load_playbooks,
)

_BODY = """playbooks:
  {name}:
    version: "0.1"
    market_groups: [코인]
    timeframe: 15m
    regimes: [RANGE]
    primary_family: LEVEL
    setups: [private_strategy]
"""


@pytest.fixture(autouse=True)
def _clean_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    forget_file_cache()


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """`_load_file` 호출을 센다 — 파싱이 실제로 몇 번 돌았나."""
    calls: list[Path] = []
    real = select._load_file  # pyright: ignore[reportPrivateUsage]

    def spy(target: Path) -> list[select.Playbook]:
        calls.append(target)
        return real(target)

    monkeypatch.setattr(select, "_load_file", spy)
    return calls


def test_same_file_is_parsed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 1 — 두 번째 호출부터는 파싱이 없고 결과는 같다."""
    made = tmp_path / "pb.yml"
    made.write_text(_BODY.format(name="probe"), encoding="utf-8")
    calls = _count_parses(monkeypatch)
    first = load_playbooks(made)
    for _ in range(38):
        assert load_playbooks(made) == first
    assert len(calls) == 1
    assert [p.playbook_id for p in first] == ["probe"]


def test_changed_file_is_reparsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 2 — 내용(크기 · mtime)이 바뀌면 다시 읽어 새 선언을 준다."""
    made = tmp_path / "pb.yml"
    made.write_text(_BODY.format(name="probe"), encoding="utf-8")
    calls = _count_parses(monkeypatch)
    assert [p.playbook_id for p in load_playbooks(made)] == ["probe"]
    made.write_text(_BODY.format(name="renamed"), encoding="utf-8")
    stat = made.stat()
    os.utime(made, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))  # mtime 2 초 뒤로
    assert [p.playbook_id for p in load_playbooks(made)] == ["renamed"]
    assert len(calls) == 2


def test_failure_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 3 — 깨진 파일은 예외를 올리고, 고친 뒤 다음 호출이 다시 읽는다."""
    made = tmp_path / "pb.yml"
    made.write_text("playbooks:\n  bad:\n    version: '0.1'\n", encoding="utf-8")
    calls = _count_parses(monkeypatch)
    with pytest.raises(PlaybookConfigError):
        load_playbooks(made)
    made.write_text(_BODY.format(name="probe"), encoding="utf-8")
    stat = made.stat()
    os.utime(made, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert [p.playbook_id for p in load_playbooks(made)] == ["probe"]
    assert len(calls) == 2


def test_returned_list_is_a_copy(tmp_path: Path) -> None:
    """부르는 쪽이 결과에 덧붙여도(DB 선언 붙이기) 캐시가 오염되지 않는다."""
    made = tmp_path / "pb.yml"
    made.write_text(_BODY.format(name="probe"), encoding="utf-8")
    books, seen = select._load_files_cached((made,))  # pyright: ignore[reportPrivateUsage]
    books.clear()
    seen.clear()
    again, seen_again = select._load_files_cached((made,))  # pyright: ignore[reportPrivateUsage]
    assert [p.playbook_id for p in again] == ["probe"]
    assert set(seen_again) == {"probe"}
