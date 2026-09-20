#!/usr/bin/env bash
# 배포된 이미지가 `추세추종+삼각수렴 1.0.0`(T291 · 1.13.0)의 다리 선언을 **실제로 읽는지** + 도는 펀드가 그대로인지.
# 이름·수·유무만 찍는다 (시크릿 없음).
set -uo pipefail
for API in $(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}"); do
echo "=== $API ==="
docker exec -i "$API" python - <<'PY'
import json
from importlib.metadata import version
from pathlib import Path

import yaml

from updown.analysis.playbook.select import load_playbooks
from updown.orchestration.rebalancer.legs import declared_legs

print("updown", version("updown"))
books = load_playbooks()
wrapper = next((b for b in books if b.playbook_id == "private_strategy"), None)
if wrapper is None:
    print("!! private_strategy 없다")
else:
    raw = yaml.safe_load(Path("config/baskets.yml").read_text(encoding="utf-8"))
    scopes = {k: [str(r["symbol"]) for r in v["members"]] for k, v in raw["by_playbook"].items()}
    legs = declared_legs(wrapper, books, scopes, scopes["private_strategy"])
    print("private_strategy", wrapper.version, "| listed=", wrapper.listed, "| split_legs=", wrapper.split_legs)
    for leg in legs:
        print(
            "  다리", leg.playbook, "| 종목", len(leg.symbols), "| 격리", leg.leverage, "| 노출", leg.exposure,
            "| 자리", leg.slots, "| 상한", leg.notional_cap, "| 브레이크", leg.drawdown_brake is not None,
            "| 폭", leg.breadth_cap is not None,
        )
for path in sorted(Path("logs/funds").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    print(
        "펀드", data.get("fund_id"), "|", data.get("playbook"), "| 종목", len(data["basket"]["members"]),
        "| 판", len(data.get("runs") or {}), "| 다리", len(data.get("legs") or []),
    )
PY
done
