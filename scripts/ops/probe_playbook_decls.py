"""T349 배포 확인 (읽기 전용) — api 컨테이너 안에서 돈다.

`bash scripts/ops/remote.sh scripts/ops/probe_playbook_decls.py`.
`playbook_decls` 표 · 알렘빅 머리 · 로더의 DB 읽기 상태 · 로더가 본 매매법 수(파일/DB) ·
2.5.0 선언 · 진입 정지 설정을 찍는다.
값(시크릿)은 찍지 않는다 — 이름 · 개수 · 상태만.
"""

from __future__ import annotations

import os

import psycopg

from updown.analysis.playbook import db_source
from updown.analysis.playbook.select import load_playbooks

dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
    cur.execute("select version_num from alembic_version")
    print("ALEMBIC", [r[0] for r in cur.fetchall()])
    cur.execute("select to_regclass('public.playbook_decls')")
    print("TABLE playbook_decls", cur.fetchone()[0])
    cur.execute("select count(*) from playbook_decls")
    print("DECL_ROWS", cur.fetchone()[0])
    cur.execute("select key, value from app_settings where key = 'live_entries_halted'")
    print("HALT", cur.fetchall())

books = load_playbooks()
print("DB_SOURCE", db_source.status())
print("PLAYBOOKS total", len(books), "listed", sorted(b.playbook_id for b in books if b.listed))
k05 = next((b for b in books if b.playbook_id == "private_strategy"), None)
if k05 is None:
    print("K05 없음")
else:
    print(
        "K05",
        k05.version,
        "legs",
        list(k05.bundle),
        "brake",
        k05.drawdown_brake,
        "cap",
        k05.notional_cap,
    )
old = next((b for b in books if b.playbook_id == "private_strategy"), None)
print("R5 superseded_by", None if old is None else old.superseded_by)
