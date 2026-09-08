"""Alembic 실행 환경 (P0-4-5).

**동기 실행이다.** psycopg3 는 드라이버 하나로 런타임(async)과 Alembic(sync)을 모두
처리하므로 URL 을 그대로 쓴다 — asyncpg 를 택했다면 드라이버를 하나 더 설치하거나
이 파일을 async 로 개조해야 했다 (plan D-2).
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.schema import SchemaItem

from updown.common.db.base import Base

# import 만으로 Base.metadata 가 완성된다. 지우면 autogenerate 가 전 테이블을
# "삭제 대상"으로 인식한다.
import updown.common.db.models  # noqa: F401  isort:skip

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """DATABASE_URL 을 읽는다.

    Returns:
        DB 접속 URL.

    Raises:
        RuntimeError: 환경변수가 없는 경우. 설정 누락은 즉시 중단이다 (spec §7).

    Note:
        설정 로딩의 정식 경로는 P0-5 `common/config.py` 다. Alembic 은 애플리케이션
        부팅과 별개 프로세스로 돌아 Settings 를 거치지 않으므로 여기서 직접 읽는다.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL 이 없다. `.env.dev` 를 로드했는지 확인한다 "
            "(예: `set -a; . ./.env.dev; set +a`)."
        )
    return url


def include_object(
    obj: SchemaItem,
    name: str | None,  # noqa: ARG001
    type_: str,
    reflected: bool,  # noqa: ARG001
    compare_to: SchemaItem | None,  # noqa: ARG001
) -> bool:
    """Autogenerate 대상에서 제외할 객체를 판정한다.

    Args:
        obj: 검사 대상 스키마 객체.
        name: 객체 이름.
        type_: 객체 종류 (`table`, `column`, ...).
        reflected: DB 에서 반사된 객체인지.
        compare_to: 비교 대상.

    Returns:
        포함하면 True.

    Note:
        `candles` 를 제외한다. **Alembic autogenerate 는 `PARTITION BY` 를 만들지
        못하므로** 파티션 부모는 수기 DDL 마이그레이션(0002)이 담당한다 (plan P-8).
        여기서 걸러 두지 않으면 autogenerate 가 매번 "일반 테이블로 다시 만들자"고
        제안한다.
    """
    return not (type_ == "table" and obj.info.get("skip_autogenerate", False))


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 생성한다."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """실제 DB 에 연결해 마이그레이션을 적용한다."""
    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # 컬럼 타입 변경을 감지한다. 끄면 Numeric 정밀도 변경 같은 것이 조용히 누락된다.
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
