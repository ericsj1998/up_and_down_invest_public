#!/usr/bin/env bash
# 서버의 옛 앱 · 화면 이미지를 정리한다 (2026-11-10 · 사용자 요청). remote.sh 로 서버에서 돈다.
#
#   bash scripts/ops/remote.sh scripts/ops/prune_old_images.sh dry      # 목록만
#   bash scripts/ops/remote.sh scripts/ops/prune_old_images.sh          # 지운다
#
# 남기는 것: 저장소마다 버전 태그 최근 KEEP 개(되돌리기용) · 컨테이너가(멈춘 것 포함) 쓰는 이미지 · 앱 · 화면이 아닌 이미지 전부.
# `docker rmi` 는 -f 없이 부른다 — 쓰는 이미지는 도커가 스스로 거절한다.
set -u
KEEP=3
DRY="${1:-}"
REPOS=$(docker ps --format '{{.Image}}' | grep -E '/(app|web):v' | cut -d: -f1 | sort -u)
if [ -z "$REPOS" ]; then
  echo "앱 이미지 저장소를 못 찾았다 — 아무것도 안 지운다"
  exit 1
fi
used=$(docker ps -a --format '{{.Image}}' | sort -u)
for repo in $REPOS; do
  # 🔴 만든 시각으로 줄 세운다 — 버전 문자열로 정렬하면 옛 날짜 태그(v2026.09.05)가 v1.44.0 보다 "최신" 이 된다.
  tags=$(docker images "$repo" --format '{{.CreatedAt}}|{{.Tag}}' | grep -E '\|v[0-9]' | sort | cut -d'|' -f2)
  total=$(echo "$tags" | grep -c .)
  keep=$(echo "$tags" | tail -n "$KEEP")
  removed=0
  skipped=0
  for tag in $tags; do
    image="$repo:$tag"
    if echo "$keep" | grep -qx "$tag"; then
      continue
    fi
    if echo "$used" | grep -qx "$image"; then
      skipped=$((skipped + 1))
      continue
    fi
    if [ "$DRY" = "dry" ]; then
      removed=$((removed + 1))
    elif docker rmi "$image" > /dev/null 2>&1; then
      removed=$((removed + 1))
    else
      skipped=$((skipped + 1))
    fi
  done
  echo "${repo##*/} $total 개 · 남김 $(echo "$keep" | tr '\n' ' ')· ${DRY:+미리 보기 }지움 $removed · 사용 중이라 남김 $skipped"
done
if [ "$DRY" != "dry" ]; then
  docker image prune -f 2>&1 | tail -1
fi
df -h / | sed 1d
