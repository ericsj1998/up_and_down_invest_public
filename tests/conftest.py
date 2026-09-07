"""`db` 마크 테스트가 공유하는 테스트 DB 준비.

**왜 conftest 인가**: 테스트 DB 생성·마이그레이션을 `test_migrations.py` 가 갖고 있으면
다른 모듈이 그 **부수효과에 얹혀 가게** 된다. 로컬에서는 이전 실행이 남긴 DB 때문에
통과하고, 빈 DB(CI) 에서는 알파벳 순서상 먼저 도는 모듈이 `database does not exist` 로
깨진다 — 실제로 P0-6 에서 그렇게 깨졌다.

**전용 테스트 DB 를 쓴다.** 왕복 테스트가 `downgrade base` 로 전 테이블을 지우므로,
dev DB 를 쓰면 P0-8 이 적재한 백필이 날아간다.

`DATABASE_URL` 이 없으면 skip 한다 — 컨테이너 없이 `pytest` 를 돌리는 경우를 막지 않는다.
"""

import contextlib
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """dev DB 이름 뒤에 `_test` 를 붙인 URL.

    Returns:
        테스트 전용 DB 의 접속 URL.

    Note:
        **세션당 한 번만 계산한다.** 매번 파생하면 `_test` 가 중복해서 붙어
        `updown_test_test` 를 찾게 된다.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL 이 없다 — `make up` 후 `.env.dev` 를 로드하면 실행된다")
    # 파괴적 테스트이므로 dev DB 를 절대 건드리지 않는다.
    return re.sub(r"/([^/?]+)(\?|$)", r"/\1_test\2", url)


@pytest.fixture(scope="session")
def migrated_test_database(test_database_url: str) -> str:
    """테스트 DB 를 만들고 `upgrade head` 까지 마친다.

    Args:
        test_database_url: 테스트 DB URL.

    Returns:
        마이그레이션이 끝난 DB 의 URL.

    Note:
        `DATABASE_URL` 은 **alembic 실행 동안만** 덮어쓰고 곧바로 되돌린다. 세션 내내
        덮어 두면 이 값을 읽어 `_test` 를 붙이는 다른 픽스처가 `_test_test` 를 만든다.
    """
    admin_url = re.sub(r"/[^/?]+(\?|$)", r"/postgres\1", test_database_url)
    db_name = test_database_url.rsplit("/", 1)[-1].split("?")[0]

    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_database_url
    try:
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous

    return test_database_url


@pytest.fixture(scope="session")
def migrated_engine(migrated_test_database: str) -> Iterator[Engine]:
    """마이그레이션이 끝난 테스트 DB 의 동기 엔진.

    Args:
        migrated_test_database: 준비된 DB URL.

    Yields:
        SQLAlchemy 엔진.
    """
    engine = sa.create_engine(migrated_test_database)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# 테스트 진행률 — `make progress` 에 pytest 도 나타나게 한다
#
# 측정 스크립트만 진행률을 남기면 "지금 뭐가 돌고 있나"를 한 곳에서 볼 수 없다.
# 테스트 스위트는 지금 16초지만 통합 테스트(`make test-all`)는 외부 API 를 실제로 치고,
# P1-8 백테스트 테스트가 붙으면 더 길어진다.
#
# ⛔ **진행률 기록이 테스트를 깨뜨리지 않는다.** 기록기 로딩·쓰기 실패는 전부 삼킨다 —
#    관측 수단이 관측 대상을 죽이면 안 된다. 이것은 spec §7 "조용한 실패 금지" 의 예외가
#    아니다: 여기서 실패하는 것은 **보조 표시**이고, 그 실패로 테스트 결과가 바뀌면
#    오히려 더 나쁜 조용한 실패가 된다.
# ---------------------------------------------------------------------------

_PROGRESS_ENV = "UPDOWN_TEST_PROGRESS"
"""`0` 이면 진행률 기록을 끈다. CI 처럼 파일을 남기고 싶지 않은 환경용."""


class _Recorder(Protocol):
    """`scripts/_progress.ProgressRecorder` 중 여기서 쓰는 부분만.

    Note:
        경로 로딩이라 실제 타입을 import 할 수 없다. 구조만 선언해 두면 pyright 가
        호출부를 검사해 준다 — `object` 로 두면 오타가 런타임까지 산다.
    """

    def update(self, done: int, *, force: bool = False) -> None:
        """진행량 갱신."""
        ...

    def finish(self, note: str = ...) -> None:
        """종료 기록."""
        ...


_recorder: _Recorder | None = None
_completed = 0

#: 기록 실패를 삼키는 컨텍스트. 관측 수단이 관측 대상을 죽이면 안 된다.
_quiet = contextlib.suppress(Exception)


def _load_recorder(total: int) -> _Recorder | None:
    """`scripts/runtime/_progress.py` 를 경로로 불러와 기록기를 만든다.

    Args:
        total: 수집된 테스트 수.

    Returns:
        기록기. 만들 수 없으면 None.

    Note:
        `scripts/` 는 패키지가 아니라 경로로 로드한다. 실패하면 **조용히 포기**한다 —
        진행률 때문에 테스트가 안 도는 것이 훨씬 나쁘다.
    """
    if os.environ.get(_PROGRESS_ENV) == "0":
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_updown_progress", REPO_ROOT / "scripts" / "_progress.py"
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        recorder: _Recorder = module.ProgressRecorder(  # pyright: ignore[reportAny]
            "pytest", total, unit="개", root=REPO_ROOT / "logs" / "progress", echo=False
        )
    except Exception:
        return None
    return recorder


def pytest_collection_finish(session: pytest.Session) -> None:
    """수집이 끝나면 전체 개수를 알 수 있다 — 그때 기록기를 만든다."""
    global _recorder, _completed
    _completed = 0
    _recorder = _load_recorder(len(session.items))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """테스트 하나가 끝날 때마다 진행량을 올린다.

    Note:
        `call` 단계만 센다. `setup`/`teardown` 까지 세면 개수가 3배가 되어 수집 개수와
        맞지 않는다. skip 은 `setup` 에서 끝나므로 그것만 따로 받는다.
    """
    if _recorder is None:
        return
    counts = report.when == "call" or (report.when == "setup" and report.skipped)
    if not counts:
        return
    global _completed
    _completed += 1
    with _quiet:
        _recorder.update(_completed)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    """종료를 남긴다 — 없으면 `make progress` 가 아직 도는 중으로 오해한다."""
    if _recorder is None:
        return
    with _quiet:
        _recorder.finish("완료" if exitstatus == 0 else f"중단: exit {exitstatus}")
