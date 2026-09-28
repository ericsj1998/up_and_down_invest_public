"""배포된 이미지가 1.25.0 선언(T316 MACD 숏 SMA50 거리)을 실제로 읽는지 — 배포 뒤 확인(값만).

bash scripts/ops/remote.sh scripts/ops/probe_125_decl.py
"""

from importlib.metadata import version

from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks

print("updown", version("updown"))
rules = load_rules()
ms = rules["private_strategy"]
print(
    "private_strategy",
    ms.version,
    {k: v for k, v in ms.params.items() if k.startswith(("sma_dist_", "momentum_"))},
)
print("private_strategy", rules["private_strategy"].version)
print("private_strategy", rules["private_strategy"].version)
books = {b.playbook_id: b for b in load_playbooks()}
print("private_strategy", books["private_strategy"].attribution)
bundle = books.get("private_strategy")
print("private_strategy legs_revision", None if bundle is None else bundle.legs_revision)
