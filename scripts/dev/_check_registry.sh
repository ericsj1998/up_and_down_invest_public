#!/usr/bin/env bash
# 새 탐지기 플러그인이 레지스트리에 붙었나 — 패치 적용 + 재설치 + 린트 + 확인.
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
for f in logs/_patch_daily.py logs/_patch_daily2.py; do [ -f "$f" ] && python3 "$f" && rm "$f"; done
uv sync -q 2>&1 | tail -1
uv run ruff format src 2>&1 | tail -1
uv run ruff check src --output-format concise | tail -3
uv run pyright src/updown/analysis/detectors/donchian_break.py src/updown/analysis/detectors/private_strategy.py 2>&1 | grep errors
uv run python - <<'PY' 2>&1 | grep -v registry.built | tail -3
from updown.analysis.detectors.registry import discovered_detectors
from updown.analysis.playbook.select import load_playbooks
reg = dict(discovered_detectors())
print("rules:", sorted(k for k in reg if "donchian" in k or k.endswith("_1d")))
print("playbooks:", [b.playbook_id for b in load_playbooks() if b.playbook_id.endswith("_1d")])
PY
