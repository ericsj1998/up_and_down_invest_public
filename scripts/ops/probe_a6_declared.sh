#!/usr/bin/env bash
# 배포된 이미지가 a6 의 장치 둘을 **실제로 읽는지** (T286 배포 뒤 확인).
set -uo pipefail
for API in $(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}"); do
echo "=== $API ==="
docker exec -i "$API" python - <<'PY'
from updown.analysis.playbook.select import load_playbooks

books = {b.playbook_id: b for b in load_playbooks()}
print("선언 수:", len(books))
b = books.get("private_strategy")
if b is None:
    print("!! private_strategy 가 없다")
else:
    print(
        "a6:", b.version,
        "| listed=", b.listed,
        "| slots=", b.slots,
        "| lev=", b.leverage,
        "| cap=", b.notional_cap,
        "| fit=", b.notional_fit,
        "| brake=", None if b.drawdown_brake is None else (str(b.drawdown_brake.at), str(b.drawdown_brake.scale)),
    )
    print("label:", b.label)
PY
done
