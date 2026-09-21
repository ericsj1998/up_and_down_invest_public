#!/usr/bin/env bash
# 커밋 도우미 — 메시지는 파일로, 경로는 인자로. pre-commit 이 고치면 같은 경로를 다시 담아 한 번 더.
#   wsl.exe -e bash scripts/dev/_commit.sh <메시지 파일> <경로…>
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
msg="$1"
shift
git add -- "$@"
if ! git commit -q -F "$msg"; then
  echo "── pre-commit 이 고쳤다 · 같은 경로를 다시 담는다"
  git add -- "$@"
  git commit -q -F "$msg" || { echo "❌ 커밋 실패"; git status --short | head -20; exit 1; }
fi
git log --oneline -1
git push -q origin dev && echo "pushed"
