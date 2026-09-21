#!/usr/bin/env bash
# v11~ "감싸는 선" 삼각수렴 그림 — 인자 = 꼬리표
set -uo pipefail
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
cd /home/ericsj1998/projects/up_and_down_invest
TAG="${1:-v11}"
S=scripts/research/scenarios
F="$S/t279_triangle_hull.py $S/t279_triangle_gallery.py"
uv run ruff format $F >/dev/null
uv run ruff check --fix --unsafe-fixes $F >/dev/null
uv run ruff check $F || { echo RUFF_FAIL; exit 1; }
set -a; . ./.env.dev; set +a
date +%T
uv run python $S/t279_triangle_gallery.py --hull "--tag=$TAG" 2>/tmp/hull.err; echo "EXIT=$?"
grep -v "registry.built\|Warning\|warn" /tmp/hull.err | tail -n 8
date +%T
