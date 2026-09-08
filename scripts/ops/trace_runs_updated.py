"""열린 판 전부의 마지막 저장 시각 — 걸음이 멈춘 판이 하나인지 전부인지 (api 컨테이너 안 · 값은 시각뿐).

bash scripts/ops/remote.sh scripts/ops/trace_runs_updated.py
"""

# ruff: noqa: E501 — 운영 출력 한 줄이 길다 (사람이 읽는 표)
from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
with psycopg.connect(DSN) as conn:
    rows = conn.execute(
        "select key, symbol, updated_at, (meta_json->'pending_entry') is not null as pending "
        "from wf_runs where closed_at is null order by symbol"
    ).fetchall()
    now = datetime.now(UTC)
    for key, symbol, updated, pending in rows:
        age = (now - updated.astimezone(UTC)).total_seconds() / 60 if updated else None
        print(
            f"{symbol:10s} {key} updated {updated.astimezone(UTC).strftime('%H:%M:%S') if updated else '—'} "
            f"({age:.0f}분 전) pending={pending}"
        )
    print("now", now.strftime("%H:%M:%S"))
