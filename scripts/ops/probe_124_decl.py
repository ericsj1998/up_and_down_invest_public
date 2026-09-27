"""배포된 이미지가 1.24.0 선언(T0.0 · E-L · T307)을 실제로 읽는지 — 배포 뒤 확인(값만).

bash scripts/ops/remote.sh scripts/ops/probe_124_decl.py
"""

from importlib.metadata import version

from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks

print("updown", version("updown"))
rules = load_rules()
bb = rules["private_strategy"]
print(
    "private_strategy",
    bb.version,
    "sl_atr=",
    bb.params.get("sl_atr"),
    "floor_sl_atr=",
    bb.params.get("floor_sl_atr"),
    "entry_stop_floor_pct=",
    bb.params.get("entry_stop_floor_pct"),
)
ml = rules["private_strategy"]
print(
    "private_strategy",
    ml.version,
    {k: v for k, v in ml.params.items() if k.startswith("vol_size_")},
)
books = {b.playbook_id: b for b in load_playbooks()}
leg = books["private_strategy"]
print(
    "private_strategy",
    leg.attribution,
    "depth_tilt=",
    leg.depth_tilt,
    "new_high_tilt=",
    leg.new_high_tilt,
)
bundle = books.get("private_strategy")
print("private_strategy legs_revision", None if bundle is None else bundle.legs_revision)
