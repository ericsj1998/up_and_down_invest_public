#!/usr/bin/env bash
# 전체 점검 — PowerShell 경유 시 $ 확장 문제를 피하려고 파일로 둔다.
set -uo pipefail
cd ~/projects/up_and_down_invest
export PATH="$HOME/.local/bin:$PATH"

uv run ruff check --fix . --output-format=concise > /tmp/r.txt 2>&1
uv run ruff format src tests scripts > /dev/null 2>&1
uv run ruff check . --output-format=concise > /tmp/r.txt 2>&1
uv run pyright > /tmp/p.txt 2>&1
uv run pytest -q -m "not integration" > /tmp/t.txt 2>&1
uv run lint-imports > /tmp/i.txt 2>&1

echo "=== ruff ==="
tail -5 /tmp/r.txt
echo "=== pyright ==="
tail -1 /tmp/p.txt
echo "=== pytest ==="
tail -3 /tmp/t.txt
echo "=== imports ==="
tail -1 /tmp/i.txt
