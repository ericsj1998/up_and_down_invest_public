"""DB 가 없으면 만든다 — 데모 API 의 별도 DB(`<POSTGRES_DB>_demo`) 용 (T221 · 2026-09-05).

`python -m updown.common.db.ensure_db` 로 `migrate_demo` 서비스가 `alembic upgrade head` 앞에 돈다.
`DATABASE_URL` 의 DB 이름이 대상이고, 같은 서버의 `postgres` DB 에 붙어 `CREATE DATABASE` 한다.

⚠️ 기존 볼륨에서는 `docker-entrypoint-initdb.d` 가 다시 돌지 않는다 — 그래서 이 스크립트가 있다.
🔴 실계좌 DB 를 건드리지 않는다: 이름이 다른 DB 하나를 **없을 때만** 만든다. DROP 은 없다.
"""

from __future__ import annotations

import os
import sys
import urllib.parse as up

import psycopg
from psycopg import sql


def main() -> int:
    """대상 DB 가 없을 때만 만든다 — `migrate_demo` 의 `alembic upgrade head` 앞에 돈다.

    Returns:
        종료 코드. DB 가 이미 있거나 새로 만들었으면 0, `DATABASE_URL` 에 DB 이름이 없으면 1.
        서버 연결 실패는 psycopg 예외로 그대로 터진다 — 데모 DB 없이 조용히 기동하는 것보다
        낫다 (규칙 #8).
    """
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
    parts = up.urlsplit(url)
    target = (parts.path or "/").lstrip("/")
    if not target:
        print("DATABASE_URL 에 DB 이름이 없다", file=sys.stderr)
        return 1
    admin = up.urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (target,)).fetchone()
        if exists:
            print(f"db ok: {target}")
            return 0
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
        print(f"db created: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
