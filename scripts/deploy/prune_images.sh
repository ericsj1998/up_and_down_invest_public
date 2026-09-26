#!/usr/bin/env bash
# 옛 앱 · 웹 이미지 정리 — 버전 태그 최신 KEEP(기본 2)개와 컨테이너가 쓰는 이미지만 남긴다 (2026-09-26 · T310 R1).
#
#   서버에서:   KEEP=2 bash scripts/deploy/prune_images.sh     (ship.sh 7단계가 배포 뒤 부른다)
#   로컬에서:   bash scripts/ops/remote.sh scripts/deploy/prune_images.sh
#
# 왜: 배포마다 app 약 650 MB + web 80 MB 를 싣고 지운 적이 없어 서버 디스크가 89%(이미지 144개 · 27 GB 중 23.9 GB 안 씀)가 됐다.
#     디스크가 차면 postgres 가 먼저 죽는다 — 실계좌가 멈춘다.
# ⛔ 강제 삭제(-f)를 안 쓴다 — 컨테이너(멈춘 블루그린 슬롯 포함)가 쓰는 이미지는 docker 가 거절하고 남는다.
#    지운 태그는 git 태그에서 언제든 다시 빌드한다. 버전 모양(vX.Y.Z)이 아닌 태그는 안 건드린다.
set -uo pipefail
KEEP="${KEEP:-2}"
IMG="ghcr.io/ericsj1998/up_and_down_invest"
echo "=== 이미지 정리 전: $(df -h / | tail -1 | awk '{print $3 " / " $2 " · " $5}')"
for repo in "$IMG/app" "$IMG/web"; do
  tags="$(docker images "$repo" --format '{{.Tag}}' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V -r)"
  [ -z "$tags" ] && continue
  gone=0
  kept_in_use=""
  for t in $(echo "$tags" | tail -n +$((KEEP + 1))); do
    if docker rmi "$repo:$t" >/dev/null 2>&1; then
      gone=$((gone + 1))
    else
      kept_in_use="$kept_in_use $t"
    fi
  done
  echo "$(basename "$repo"): 남김 $(echo "$tags" | head -n "$KEEP" | tr '\n' ' ')· 지움 ${gone}개${kept_in_use:+ · 쓰는 중이라 남김$kept_in_use}"
done
docker image prune -f >/dev/null  # 이름 없는(dangling) 층만
echo "=== 이미지 정리 뒤: $(df -h / | tail -1 | awk '{print $3 " / " $2 " · " $5}')"
