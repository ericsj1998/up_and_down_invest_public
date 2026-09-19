#!/usr/bin/env bash
# 실계좌 펀드 파일의 **규칙 필드만** 찍는다 (T286 배포 전후 대조).
# 값은 규칙뿐이다 — 잔고·키·주소는 안 찍는다.
set -uo pipefail
for API in $(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}"); do
echo "=== $API ==="
docker exec -i "$API" python - <<'PY'
import json, pathlib
root = pathlib.Path("logs/funds")
for path in sorted(root.glob("*.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    print(
        path.name,
        "| playbook=", d.get("playbook"),
        "| weight_mode=", d.get("weight_mode"),
        "| slots=", d.get("slots"),
        "| halt=", d.get("halt_after_stops"),
        "| cap=", d.get("notional_cap"),
        "| fit=", d.get("notional_fit"),
        "| brake=", d.get("drawdown_brake"),
        "| members=", len(((d.get("basket") or {}).get("members")) or []),
    )
PY
done
