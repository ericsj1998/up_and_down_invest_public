#!/usr/bin/env bash
# 배포된 이미지가 A+(T289 · 1.12.0)의 `breadth_cap` 과 묶음을 **실제로 읽는지** — 배포 뒤 확인.
set -uo pipefail
for API in $(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}"); do
echo "=== $API ==="
docker exec -i "$API" python - <<'PY'
from importlib.metadata import version

from updown.analysis.playbook.select import load_playbooks

print("updown", version("updown"))
books = {b.playbook_id: b for b in load_playbooks()}
for name in ("private_strategy", "private_strategy"):
    b = books.get(name)
    if b is None:
        print("!!", name, "없다")
        continue
    cap = b.breadth_cap
    print(
        name, b.version,
        "| listed=", b.listed,
        "| slots=", b.slots,
        "| lev=", b.leverage,
        "| cap=", b.notional_cap,
        "| fit=", b.notional_fit,
        "| breadth=", None if cap is None else (cap.min, str(cap.cap), cap.bars),
    )
PY
done
