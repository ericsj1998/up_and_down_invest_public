"""돌던 펀드 저장본의 다리 값 · 개정 번호 — 배포 전후 대조용(읽기만 · 주소 · 시크릿 없음).

    bash scripts/ops/remote.sh scripts/ops/probe_fund_legs.py

배포로 묶음 `legs_revision` 이 오르면 되살릴 때 다리 계좌 층 값을 선언에서 다시 읽는다(411차) —
새 칸 말고 다른 값까지 바뀌지 않는지 이 출력과 로컬 선언을 대조한다.
"""

import json
import os
from pathlib import Path

root = Path(os.environ.get("FUNDS_ROOT", "logs/funds"))
for path in sorted(root.glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    if not isinstance(data, dict) or not data.get("legs"):
        continue
    fund, book = data.get("fund_id"), data.get("playbook")
    print("FUND", fund, book, "legs_revision", data.get("legs_revision"))
    for leg in data["legs"]:
        body = {k: v for k, v in leg.items() if k != "symbols"}
        body["symbols_n"] = len(leg.get("symbols") or [])
        print("LEG", json.dumps(body, ensure_ascii=False, sort_keys=True))
