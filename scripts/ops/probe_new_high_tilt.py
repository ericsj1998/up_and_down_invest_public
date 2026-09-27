"""배포된 이미지가 돌파 롱 전고점 기울이기(1.22.0 · 447차)를 **실제로 읽는지** — 배포 뒤 확인.

bash scripts/ops/remote.sh scripts/ops/probe_new_high_tilt.py
"""

from importlib.metadata import version

from updown.analysis.playbook.select import load_playbooks

print("updown", version("updown"))
books = {b.playbook_id: b for b in load_playbooks()}
for name in ("private_strategy", "private_strategy", "private_strategy"):
    b = books.get(name)
    if b is None:
        print("!!", name, "없다")
        continue
    print(
        name,
        b.attribution,
        "| new_high_tilt=",
        b.new_high_tilt,
        "| entry_limit=",
        b.entry_limit,
        "| entry_exposure_cap=",
        b.entry_exposure_cap,
    )
bundle = books.get("private_strategy")
print("private_strategy legs_revision", None if bundle is None else bundle.legs_revision)
