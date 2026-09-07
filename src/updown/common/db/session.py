"""async 엔진·세션 팩토리 (spec §2.1, plan D-2).

드라이버는 **psycopg3** 하나다 (`postgresql+psycopg://`). 런타임은 async, Alembic 은
같은 드라이버의 sync 모드를 쓴다 — asyncpg 를 택했다면 Alembic 용 드라이버를 하나 더
설치하거나 `env.py` 를 async 로 개조해야 했다 (plan D-2).

URL 을 인자로 받는 이유: 설정 로딩은 P0-5 `common/config.py` 의 책임이다. 여기서
환경변수를 직접 읽으면 P0-5 에서 두 경로가 생긴다.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """비동기 엔진을 만든다.

    Args:
        database_url: `postgresql+psycopg://user:pw@host:port/db` 형식.
        echo: SQL 로그 출력 여부. 운영에서는 끈다 — 쿼리 파라미터에 시크릿이 섞일 수
            있고(spec §8), 로그량도 감당이 안 된다.

    Returns:
        커넥션 풀이 붙은 async 엔진.

    Raises:
        ValueError: URL 이 psycopg3 드라이버를 가리키지 않는 경우.

    Note:
        드라이버를 검증하는 이유는 조용한 실패 방지다 (spec §7). `postgresql://` 로
        두면 SQLAlchemy 가 psycopg2 를 찾다가 런타임에야 실패하고, 그때는 이미
        기동 중이라 원인이 멀어진다.
    """
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError(
            "DATABASE_URL 은 psycopg3 드라이버여야 한다 (plan D-2): "
            f"'postgresql+psycopg://...' 형식. 받은 값의 스킴: {database_url.split('://')[0]!r}"
        )
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,  # 유휴 커넥션이 죽은 채로 잡히는 것을 막는다
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """세션 팩토리를 만든다.

    Args:
        engine: `create_engine` 이 만든 엔진.

    Returns:
        세션 팩토리.

    Note:
        `expire_on_commit=False` 인 이유: 커밋 후에도 ORM 객체의 값을 읽어야 하는데
        (로그 적재·이벤트 발행), 기본값이면 그 접근이 추가 쿼리를 유발하고 세션이
        닫힌 뒤에는 아예 실패한다.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """트랜잭션 경계를 감싼다.

    Args:
        factory: 세션 팩토리.

    Yields:
        세션. 블록이 정상 종료하면 커밋, 예외가 나면 롤백한다.

    Raises:
        Exception: 블록 안에서 난 예외는 롤백한 뒤 **그대로** 다시 던진다 — 여기서 삼키면
            호출자가 커밋된 줄 알고 진행한다 (조용한 실패 금지, 규칙 #8).

    Note:
        **로그 적재에는 이 스코프를 쓰지 않는다.** 주 로직과 같은 트랜잭션에 로그를
        묶으면 로그 실패가 주 로직을 되돌린다 — spec §1.2.1 이 금지하는 바로 그
        역전이다. `event_logs` 싱크는 별도 세션을 쓴다 (P0-6).
    """
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
