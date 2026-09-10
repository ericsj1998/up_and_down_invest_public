#!/usr/bin/env bash
# 공개본 README 의 상대 링크가 내보낸 트리에 실제로 있는지 — 매 내보내기 뒤 돈다.
set -u
DEST="${1:-$HOME/projects/up_and_down_invest_public}"
cd "$DEST" || exit 1
missing=0
while IFS= read -r link; do
  path="${link%%#*}"
  [ -z "$path" ] && continue
  case "$path" in http*|mailto*) continue;; esac
  if [ ! -e "$path" ]; then echo "MISSING $path"; missing=$((missing+1)); fi
done < <(grep -oE '\]\([^)]+\)' README.md | sed -E 's/^\]\(//; s/\)$//' | sort -u)
echo "readme links missing: $missing"
[ "$missing" = 0 ]
