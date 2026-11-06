"""이번 분기 종목 목록을 다리별로 찍는다(읽기만).

행 번호 · 규칙 지문 · 판 수 · 이름 사슬에서 빠진 것.

`bash scripts/ops/remote.sh scripts/ops/probe_universe_list.py`
"""

import json
import os

import psycopg


def main() -> None:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as db:
        rows = db.execute(
            "select id, quarter, profile, status, rule_digest, legs_gate, source, reason "
            "from universe_snapshots order by id desc limit 3"
        ).fetchall()
    print("행", len(rows))
    for r in rows:
        print("행", r[0], r[1], r[2], r[3], str(r[4])[:12], r[7] or "")
    ok = next((r for r in rows if r[3] == "ok"), None)
    if ok is None:
        return
    legs = json.loads(ok[5]) if isinstance(ok[5], str) else ok[5]
    union: set[str] = set()
    for name, syms in sorted(legs.items()):
        if not name.startswith("private_strategy"):
            union |= set(syms)
        print(f"  {name}: {len(syms)} · {' '.join(s.replace('_USDT', '') for s in syms)}")
    print("세션 판 수", len(union))
    src = json.loads(ok[6]) if isinstance(ok[6], str) else ok[6]
    print("이름 사슬에서 빠짐", (src or {}).get("name_chain_dropped"))


main()
