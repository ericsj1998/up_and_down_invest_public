#!/usr/bin/env bash
# 한 줄 배포 — 블루그린 대신 **옛 API 를 먼저 내리고** 새 API 하나만 올린다 (2026-09-29 · 1 GB 서버).
#
#   bash scripts/deploy/ship_stop_first.sh          # main 에서 · 태그 = v<pyproject 버전>
#
# 왜: 1 GB 서버에서 블루그린은 두 슬롯을 겹쳐 띄우다 메모리로 롤백한다(v1.18.1 · `bluegreen-fails-on-1gb`).
# 포지션 · 대기 주문 · 손절이 **0** 일 때는 몇 분 멈추고 바꾸는 편이 안전하다(`scripts/ops/swap_stop_first.sh`).
# 이 스크립트는 `ship.sh` 의 0 ~ 3 · 5 ~ 7 단계를 그대로 쓰고 4단계만 바꾼다:
#   4a) IMAGE_TAG 갱신 4b) 마이그레이션(`migrate_live.sh` — stop-first 는 마이그레이션을 안 돈다) 4c) 거래소 0 확인(아니면 멈춤)
#   4d) `swap_stop_first.sh`.
# 🔴 거래소에 포지션 · 대기 주문 · 손절 주문이 하나라도 있으면 **바꾸지 않고 멈춘다** — 그때는 `ship.sh`(블루그린)를 쓴다.
set -euo pipefail
cd "$(dirname "$0")/../.."
HERE="scripts/ops"
[ -f "$HERE/host.env" ] && . "$HERE/host.env"
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
REMOTE_DIR="${DEPLOY_DIR:-~/updown}"
IMG=ghcr.io/ericsj1998/up_and_down_invest
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15 $HOST"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = "main" ] || { echo "ERROR: 배포는 main 에서만 (지금 $BRANCH)"; exit 1; }
VERSION=$(grep -E '^version = ' pyproject.toml | head -1 | cut -d'"' -f2)
TAG="${1:-v$VERSION}"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "ERROR: 태그 $TAG 가 이미 있다 — pyproject.toml 의 version 을 올리고 CHANGELOG.md 에 적는다"; exit 1
fi
echo "=== ship(stop-first) $TAG → $HOST"

echo "=== 0) 작업 트리 · docker · 엔진 갭"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ERROR: 커밋 안 된 변경이 있다"; git status --short --untracked-files=no | head; exit 1
fi
docker info >/dev/null 2>&1 || { echo "ERROR: docker 데몬에 붙을 수 없다"; exit 1; }
if [ "${SKIP_GAP_CHECK:-0}" != "1" ]; then
  if ! GAP_OUT=$(uv run python scripts/build/engine_gap.py --strict 2>&1); then
    echo "$GAP_OUT" | grep -v registry; echo "ERROR: 엔진 갭 🔴"; exit 1
  fi
  echo "$GAP_OUT" | grep -v registry
fi
echo "=== 0b) 자료 출처 키 동기화"
bash scripts/ops/sync_env_keys.sh

echo "=== 1) 빌드"
docker build -q -t "$IMG/app:$TAG" . >/dev/null
docker build -q -t "$IMG/web:$TAG" web >/dev/null

echo "=== 2) 배포 파일 동기화"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" --exclude ".env*" docker/ "$HOST:$REMOTE_DIR/docker/"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" scripts/deploy/ "$HOST:$REMOTE_DIR/scripts/deploy/"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" Makefile "$HOST:$REMOTE_DIR/"

echo "=== 3) 이미지 전송"
docker save "$IMG/app:$TAG" "$IMG/web:$TAG" | gzip -1 | $SSH "nice -n 19 gunzip | nice -n 19 docker load" | tail -2

echo "=== 4c) 거래소 0 확인 (포지션 · 대기 주문 · 손절)"
PROBE=$(bash scripts/ops/remote.sh scripts/ops/probe_gate.py 2>&1 | grep -E '^(POSITIONS|OPEN_ORDERS|STOP_ORDERS) ' || true)
echo "$PROBE"
for key in POSITIONS OPEN_ORDERS STOP_ORDERS; do
  n=$(echo "$PROBE" | grep -E "^$key " | awk '{print $2}')
  if [ "$n" != "0" ]; then
    echo "🔴 $key = ${n:-모름} — 멈추고 바꾸지 않는다(이미지는 서버에 있다 · ship.sh 블루그린을 쓰거나 0 이 된 뒤 다시)"; exit 1
  fi
done

echo "=== 4a) IMAGE_TAG 갱신"
$SSH "cd $REMOTE_DIR && sed -i \"s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/\" .env.live && grep -E '^IMAGE_TAG=' .env.live"

echo "=== 4b) 마이그레이션 (라이브 DB)"
bash scripts/ops/remote.sh scripts/ops/migrate_live.sh

echo "=== 4d) 옛 API 먼저 내리고 바꾸기"
bash scripts/ops/remote.sh scripts/ops/swap_stop_first.sh

echo "=== 5) 태그"
git tag "$TAG" >/dev/null && git push -q origin "$TAG" && echo "tag $TAG pushed"

echo "=== 6) 밖에서 확인"
curl -s -o /dev/null -w "https health: %{http_code}\n" --max-time 20 "https://${PUBLIC_DOMAIN:?}/api/health"
echo "✅ $TAG 배포 완료(stop-first)"

echo "=== 7) 옛 이미지 정리 (PRUNE=1 일 때만 — 배포 직후 CPU 를 아낀다 · 평소엔 조용한 시각에 prune_images.sh)"
if [ "${PRUNE:-0}" = "1" ]; then
  $SSH "cd $REMOTE_DIR && KEEP=2 bash scripts/deploy/prune_images.sh" || echo "⚠️ 이미지 정리 실패 — 배포는 끝났다"
else
  echo "건너뜀 — PRUNE=1 이면 지금 · 아니면 조용한 시각에: bash scripts/ops/remote.sh scripts/deploy/prune_images.sh"
fi
