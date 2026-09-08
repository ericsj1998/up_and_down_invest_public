"""engine 생존 신호 검증 (P0-9-2 · spec §12.6 데드맨 스위치).

**`db` 마크를 붙이지 않는다** — Redis·DB 가 필요 없는 순수 검사이므로 빠른 경로
(`pytest -m "not db"`)에서도 돌아야 한다. 락 테스트와 같은 파일에 두면 모듈 레벨
db 마크에 걸려 그 경로에서 빠진다.
"""

import asyncio
from pathlib import Path

from updown.apps.engine.liveness import LivenessBeacon, is_alive


def test_missing_beacon_file_is_not_alive(tmp_path: Path) -> None:
    """파일이 없으면 "루프가 돈다는 증거가 없다" 다."""
    assert is_alive(tmp_path / "absent") is False


def test_fresh_beacon_is_alive(tmp_path: Path) -> None:
    path = tmp_path / "alive"
    path.touch()
    assert is_alive(path, stale_after_seconds=30.0) is True


def test_stale_beacon_is_not_alive(tmp_path: Path) -> None:
    """**이벤트 루프가 멈춘 상태를 잡는 것이 이 검사의 목적이다.**

    `pgrep` 은 프로세스만 보므로 루프가 죽어도 통과한다 — engine 은 PID 1 이라 프로세스
    생존은 거의 항진명제다.
    """
    path = tmp_path / "stale"
    path.touch()
    # `now` 를 인자로 받으므로 시계를 건드리지 않고 미래를 재현한다 (원칙 P1).
    future = path.stat().st_mtime + 100.0
    assert is_alive(path, stale_after_seconds=30.0, now=future) is False


def test_beacon_boundary_is_inclusive(tmp_path: Path) -> None:
    """경계에서 깜박이지 않게 — 정확히 임계값이면 아직 살아 있다."""
    path = tmp_path / "edge"
    path.touch()
    boundary = path.stat().st_mtime + 30.0
    assert is_alive(path, stale_after_seconds=30.0, now=boundary) is True


async def test_beacon_refreshes_while_running(tmp_path: Path) -> None:
    """루프가 도는 동안 mtime 이 갱신돼야 한다."""
    beacon = LivenessBeacon(tmp_path / "beat", interval_seconds=0.05)
    await beacon.start()
    try:
        first = beacon.path.stat().st_mtime
        await asyncio.sleep(0.25)
        assert beacon.path.stat().st_mtime > first
    finally:
        await beacon.stop()


async def test_beacon_touches_immediately_on_start(tmp_path: Path) -> None:
    """기동 직후 healthcheck 가 stale 로 보지 않아야 한다."""
    beacon = LivenessBeacon(tmp_path / "beat", interval_seconds=60.0)
    await beacon.start()
    try:
        assert beacon.path.exists()
        assert is_alive(beacon.path) is True
    finally:
        await beacon.stop()


async def test_beacon_removes_the_file_on_stop(tmp_path: Path) -> None:
    """옛 mtime 이 남으면 **아직 안 뜬 engine 을 살아 있다고 오판**한다.

    named volume 을 쓰므로 파일이 컨테이너보다 오래 산다.
    """
    beacon = LivenessBeacon(tmp_path / "beat", interval_seconds=0.05)
    await beacon.start()
    await beacon.stop()
    assert beacon.path.exists() is False
    assert is_alive(beacon.path) is False


def test_beacon_touch_failure_does_not_raise(tmp_path: Path) -> None:
    """감시 수단을 잃으려다 감시 대상까지 잃지 않는다 — engine 이 죽으면 안 된다."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()  # 디렉터리라서 touch 가 실패한다
    LivenessBeacon(blocked).touch()


async def test_beacon_stop_is_safe_when_not_started(tmp_path: Path) -> None:
    await LivenessBeacon(tmp_path / "never").stop()
