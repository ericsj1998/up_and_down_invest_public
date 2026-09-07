"""`accounts` 열 목록과 alembic 버전 — 마이그레이션이 라이브 DB 에 들어갔는지 (api 컨테이너 안).

    bash scripts/ops/remote.sh scripts/ops/check_accounts_audit.py

왜: 2026-09-06 구글 로그인 500 — `column accounts.audit does not exist`. 배포 뒤 head 인지 본다.
값은 열 이름·버전·행 수뿐이다.
"""

from __future__ import annotations

import os

import psycopg

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
with psycopg.connect(DSN) as conn:
    cols = conn.execute(
        "select column_name from information_schema.columns "
        "where table_name='accounts' order by ordinal_position"
    ).fetchall()
    print("accounts columns:", [c[0] for c in cols])
    print("audit column:", "present" if any(c[0] == "audit" for c in cols) else "MISSING")
    print(
        "alembic_version:", [v[0] for v in conn.execute("select version_num from alembic_version")]
    )
    row = conn.execute("select count(*) from accounts").fetchone()
    print("accounts rows:", row[0] if row else None)
