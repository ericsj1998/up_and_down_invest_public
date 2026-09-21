#!/usr/bin/env bash
# 파이썬 점검 한 벌 — ruff · pyright · 고른 테스트. 인자 = pytest 대상(없으면 전부).
#   wsl.exe -e bash scripts/dev/_check_py.sh tests/test_ranking_groups.py
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
echo "── ruff"
uv run ruff check . 2>&1 | tail -15
echo "── ruff format"
uv run ruff format --check . 2>&1 | tail -5
echo "── pyright"
uv run pyright 2>&1 | tail -12
echo "── pytest $*"
if [ "$#" -eq 0 ]; then
  uv run pytest -x -q 2>&1 | tail -15
else
  uv run pytest -x -q "$@" 2>&1 | tail -25
fi
