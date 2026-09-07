"""한 판의 `wf_runs` 행 — 마지막 저장 시각과 meta_json 의 작은 값들 (api 컨테이너 안 · 값은 요약만).

    SYMBOL=NEAR_USDT bash scripts/ops/remote.sh scripts/ops/trace_run_meta.py

왜: 러너가 걸음을 계속 걷고 있는지(마지막 저장 시각) · 대기 표가 살아 있는지 (2026-09-06 고아 사고).
"""

# ruff: noqa: E501 — 운영 출력 한 줄이 길다 (사람이 읽는 표)
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import psycopg

SYMBOL = os.environ.get("SYMBOL", "NEAR_USDT")
DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")

with psycopg.connect(DSN) as conn:
    cols = [
        c[0]
        for c in conn.execute(
            "select column_name from information_schema.columns where table_name='wf_runs' order by ordinal_position"
        ).fetchall()
    ]
    print("== wf_runs columns:", cols)
    rows = conn.execute(
        "select * from wf_runs where symbol = %s and closed_at is null", (SYMBOL,)
    ).fetchall()
    for row in rows:
        rec = dict(zip(cols, row, strict=True))
        meta = rec.pop("meta_json", None) or {}
        if not isinstance(meta, dict):
            meta = json.loads(meta)
        small = {k: v for k, v in rec.items() if k != "id"}
        for k, v in small.items():
            if isinstance(v, datetime):
                small[k] = v.astimezone(UTC).strftime("%m-%d %H:%M:%S")
        print("== row:", json.dumps(small, ensure_ascii=False, default=str)[:600])
        summary: dict[str, object] = {}
        for k, v in meta.items():
            if isinstance(v, (int, float, bool, str)) or v is None:
                summary[k] = v if not isinstance(v, str) else v[:60]
            elif isinstance(v, (list, dict)):
                summary[k] = f"<{type(v).__name__} {len(v)}>"
        print("== meta_json (작은 값):", json.dumps(summary, ensure_ascii=False)[:1200])
    print("now:", datetime.now(UTC).strftime("%m-%d %H:%M:%S"))
