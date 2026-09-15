"""플러그인 접점 — 탐지기·룰 설정·플레이북 선언을 **설치된 패키지에서 찾는다** (T224).

매매법은 비공개 패키지(`updown-strategy`)에, 플랫폼은 공개 저장소에 산다. 둘을 잇는 것은
import 가 아니라 파이썬 **entry points** 다 — 플랫폼 코드는 전략 모듈의 이름을 모른다.

    [project.entry-points."updown.detectors"]   탐지기: `register() -> ((rule_id, factory), …)`
    [project.entry-points."updown.rules"]       룰 설정 디렉토리: `() -> Path`
    [project.entry-points."updown.playbooks"]   플레이북 선언 파일: `() -> Path`

순서(앞이 우선): entry point → 환경 변수(`UPDOWN_RULES_DIRS` · `UPDOWN_PLAYBOOK_FILES` ·
`os.pathsep` 구분) → 저장소 기본(`config/rules` · `config/playbooks.yml`). 플레이북은 선언
순서가 곧 기본 선택(`default_playbook` = 첫 `recommended`)이라 전략 패키지가 앞에 온다.

## 왜 데코레이터가 아니라 entry points 인가

데코레이터 등록은 **import 부수효과**에 기댄다 — 모듈을 import 하지 않으면 등록이 안 되고,
그 실수는 "시험에서는 되는데 운영에서 플러그인이 없다" 로 나타난다. entry points 는 설치된
패키지의 메타데이터가 표라서 그 경로가 없고, 어느 패키지가 무엇을 등록했는지 밖에서 보인다.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import cast

from updown.analysis.detectors.base import DetectorFactory

DETECTORS_GROUP = "updown.detectors"
RULES_GROUP = "updown.rules"
PLAYBOOKS_GROUP = "updown.playbooks"
RULES_ENV = "UPDOWN_RULES_DIRS"
PLAYBOOKS_ENV = "UPDOWN_PLAYBOOK_FILES"
DEFAULT_RULES_DIR = Path("config/rules")
DEFAULT_PLAYBOOKS = Path("config/playbooks.yml")


def discovered_detectors() -> tuple[tuple[str, DetectorFactory], ...]:
    """Entry point `updown.detectors` 로 등록된 (룰 id, 팩토리) 전부.

    Returns:
        entry point 이름순으로 이어 붙인 목록. 같은 모듈이 여러 id 를 맡을 수 있다.

    Note:
        중복 id 검사는 여기서 하지 않는다 — `SetupRegistry` 가 잡는다. 여기서 거르면 두 패키지가
        같은 id 를 등록한 사실이 조용히 사라진다.
    """
    out: list[tuple[str, DetectorFactory]] = []
    for point in sorted(entry_points(group=DETECTORS_GROUP), key=lambda item: item.name):
        register = point.load()
        pairs = cast("Iterable[tuple[str, DetectorFactory]]", register())
        out.extend(pairs)
    return tuple(out)


def _from_env(name: str) -> list[Path]:
    """환경 변수의 `os.pathsep` 구분 경로 목록. 비었거나 없으면 빈 목록."""
    raw = os.environ.get(name, "")
    return [Path(part) for part in raw.split(os.pathsep) if part.strip()]


def _from_entry_points(group: str) -> list[Path]:
    """그 그룹의 entry point 들이 가리키는 경로 — 이름순이라 순서가 설치 순서에 안 흔들린다."""
    found: list[Path] = []
    for point in sorted(entry_points(group=group), key=lambda item: item.name):
        locate = point.load()
        found.append(Path(cast("Path | str", locate())))
    return found


def _unique(paths: Iterable[Path]) -> tuple[Path, ...]:
    """같은 곳을 가리키는 경로를 한 번만 남긴다 — 순서는 첫 등장 기준.

    entry point 와 환경 변수·기본값이 같은 디렉토리를 가리키면(로컬 개발에서 흔하다) 같은 룰이
    두 번 읽힌다. 같음은 `resolve()` 로 재되 돌려주는 것은 원래 표기다 — 로그·오류에 그 표기가
    실린다.

    Args:
        paths: 우선순위 순의 경로들.

    Returns:
        중복을 뺀 경로들 (원래 표기 · 원래 순서).
    """
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return tuple(out)


def rule_dirs(explicit: Path | None = None) -> tuple[Path, ...]:
    """룰 설정 디렉토리들 — `load_rules` 가 이 순서로 합친다.

    Args:
        explicit: 이 디렉토리만 (시험·도구용). None 이면 접점 순서대로 전부.

    Returns:
        존재 여부와 무관한 경로 목록. 없는 디렉토리는 `load_rules` 가 건너뛴다.
    """
    if explicit is not None:
        return (explicit,)
    return _unique([*_from_entry_points(RULES_GROUP), *_from_env(RULES_ENV), DEFAULT_RULES_DIR])


def playbook_files(explicit: Path | None = None) -> tuple[Path, ...]:
    """플레이북 선언 파일들 — `load_playbooks` 가 이 순서로 합친다.

    Args:
        explicit: 이 파일만 (시험·도구용). None 이면 접점 순서대로 전부.

    Returns:
        entry point → 환경 변수 → 기본 파일(있을 때만). 명시 경로는 없어도 돌려준다 —
        "없다" 는 오류는 읽는 쪽이 낸다.
    """
    if explicit is not None:
        return (explicit,)
    found = [*_from_entry_points(PLAYBOOKS_GROUP), *_from_env(PLAYBOOKS_ENV)]
    if DEFAULT_PLAYBOOKS.exists():
        found.append(DEFAULT_PLAYBOOKS)
    return _unique(found)
