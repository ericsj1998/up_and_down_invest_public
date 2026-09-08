#!/usr/bin/env bash
# 릴리스 한 줄 — dev 를 main 에 fast-forward 하고 push 한 뒤 ship.sh (2026-09-09 · 사용자 "앞으로 네가 돌릴 수 있게").
#   bash scripts/deploy/release.sh
# ⛔ 실계좌 배포다. 사용자 허가가 있을 때만 부른다 (CLAUDE.md · 배포는 허가 뒤).
set -eu
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
[ -z "$(git status --short --untracked-files=no)" ] || { echo "ERROR: 커밋 안 된 변경이 있다"; git status --short --untracked-files=no | head; exit 1; }
git fetch -q origin
git checkout -q main
git merge -q --ff-only dev || { echo "ERROR: main 을 dev 로 fast-forward 못 한다 — main 에 dev 에 없는 커밋이 있다"; git checkout -q dev; exit 1; }
git push -q origin main
echo "main = $(git rev-parse --short HEAD) · $(grep -m1 '^version' pyproject.toml)"
rc=0
bash scripts/deploy/ship.sh || rc=$?
git checkout -q dev
echo "=== release exit=$rc"
exit $rc
