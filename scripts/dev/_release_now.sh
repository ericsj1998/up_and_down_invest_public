#!/usr/bin/env bash
# dev → main → ship (블루그린) 을 한 번에. 로그는 logs/release_<버전>.log.
#
#   bash scripts/dev/_release_now.sh
#
# 🔴 main 은 `~/projects/updown_ship` 워크트리에 체크아웃돼 있다 — 이 저장소에서 `git checkout main`
#    은 실패한다(2026-09-17 실측). 그래서 `dev:main` 으로 밀고 워크트리에서 당겨 ship 한다.
# ⛔ 사용자가 "배포해" 라고 말했을 때만 부른다.
set -euo pipefail
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
VER="$(grep -m1 -E '^version = ' pyproject.toml | sed -E 's/version = "(.*)"/\1/')"
LOGF="logs/release_${VER}.log"
mkdir -p logs
exec > >(tee "$LOGF") 2>&1
echo "=== release v$VER 시작 $(date '+%F %T')"

[ "$(git rev-parse --abbrev-ref HEAD)" = "dev" ] || { echo "ERROR: dev 가 아니다"; exit 1; }
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ERROR: 커밋 안 된 변경이 있다"; git status --short --untracked-files=no | head; exit 1
fi
git fetch origin
# origin/main 에 dev 에 없는 커밋(GitHub 웹 수정 등)이 있으면 멈춘다 — 먼저 dev 에 병합한다.
BEHIND="$(git rev-list --count HEAD..origin/main)"
if [ "$BEHIND" -gt 0 ]; then
  echo "ERROR: origin/main 에 dev 에 없는 커밋 $BEHIND 개 — git merge origin/main 뒤에 다시"; exit 1
fi
git push origin dev
git push origin dev:main

cd /home/ericsj1998/projects/updown_ship || { echo "ERROR: ship 워크트리가 없다"; exit 1; }
# `uv run` 이 워크트리의 uv.lock 을 건드려 pull 이 막힌 적이 있다 — 그것만이면 되돌린다.
OTHER="$(git status --porcelain --untracked-files=no | grep -v ' uv.lock$' || true)"
if [ -n "$OTHER" ]; then
  echo "ERROR: ship 워크트리에 uv.lock 말고 다른 변경이 있다"; echo "$OTHER"; exit 1
fi
git checkout -- uv.lock 2>/dev/null || true
git pull --ff-only
echo "=== ship 워크트리 HEAD: $(git log --oneline -1)"
bash scripts/deploy/ship.sh
echo "=== release v$VER 끝 $(date '+%F %T')"
