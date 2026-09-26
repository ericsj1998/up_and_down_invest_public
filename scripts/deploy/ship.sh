#!/usr/bin/env bash
# 한 줄 배포 — 로컬에서 빌드해 서버로 실어 보내고 블루그린으로 교체한다 (2026-09-05).
#
#   bash scripts/deploy/ship.sh                 # main 에서 · 태그 = v<pyproject 버전> (예: v1.0.0)
#   bash scripts/deploy/ship.sh v1.0.1-rc1      # 태그 지정 (드물게)
#
# 왜 레지스트리 없이: 1 GB 서버는 빌드가 안 되고(T47), GHCR 는 private 라 서버에 PAT 가 필요하다.
# `docker save | ssh docker load` 는 둘 다 필요 없다 — 3분쯤 걸리고, 이미지가 서버에 있으면
# `bluegreen.sh` 가 pull 을 건너뛴다. 나중에 PAT 를 두면 `release-images` 워크플로 + pull 로 바꿀 수 있다.
#
# ⛔ .env.live 의 **값**은 건드리지 않는다 (LIVE_ORDERS · 키). IMAGE_TAG 한 줄만 바꾼다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

# 서버 주소·도메인은 저장소에 두지 않는다 (퍼블릭 저장소 · 2026-09-06) — scripts/ops/host.env 에서 읽는다.
[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다 — host.env.example 을 복사해 채운다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
REMOTE_DIR="${DEPLOY_DIR:-~/updown}"
IMG="ghcr.io/ericsj1998/up_and_down_invest"
SSH="ssh -i $KEY -o BatchMode=yes $HOST"

# 🔴 배포는 main 에서만 (1.0.0 · 2026-09-06). 개발은 dev 에서 갈라 하고 main 으로 합친 뒤 배포한다.
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ] && [ "${ALLOW_BRANCH:-0}" != "1" ]; then
  echo "ERROR: 배포는 main 에서만 한다 (지금 $BRANCH). 정말 필요하면 ALLOW_BRANCH=1"; exit 1
fi

# 태그 = 버전 (semver · CHANGELOG.md). pyproject.toml 의 version 이 단일 출처다.
# 같은 태그가 이미 있으면 거절한다 — 배포 = 버전을 올린 커밋 (무엇이 떠 있는지 = 태그).
TAG="${1:-}"
if [ -z "$TAG" ]; then
  TAG="v$(grep -m1 -E '^version = ' pyproject.toml | sed -E 's/version = "(.*)"/\1/')"
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "ERROR: 태그 $TAG 가 이미 있다 — pyproject.toml 의 version 을 올리고 CHANGELOG.md 에 적는다"; exit 1
fi
echo "=== ship $TAG → $HOST"

echo "=== 0) 작업 트리가 깨끗한가 (배포 = 커밋된 것만)"
# 추적 파일의 변경만 본다 — 추적 안 된 파일(예: 루트에 둔 이미지)은 .dockerignore 화이트리스트 밖이라 이미지에 안 들어간다.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ERROR: 커밋 안 된 변경이 있다 — 배포는 커밋된 상태에서만 한다"; git status --short --untracked-files=no | head; exit 1
fi

# ⛔ Docker Desktop 연동이 끊기면 WSL 의 `docker` 는 안내문만 찍고 **0 을 돌려준다** — 빌드가 된 척
#    스크립트가 조용히 끝난다 (2026-09-05 실측). 데몬에 실제로 붙는지 먼저 본다.
docker info >/dev/null 2>&1 || { echo "ERROR: docker 데몬에 붙을 수 없다 — Docker Desktop 이 꺼져 있거나 WSL 연동이 끊겼다"; exit 1; }

# 🔴 두 엔진의 갭 관문 (T233 · 2026-09-09). 세션 저장소 vs 연구 저장소(E1) 부호가 다르거나 크기 승수가 죽어 있으면(🔴) 배포하지 않는다 —
#    채택 매매법이 실계좌 엔진에서 반대 부호로 도는 것을 화면에 얹은 채 내보낸 사고(T232)의 재발 방지. 저장소가 없으면 도구가 건너뛴다.
if [ "${SKIP_GAP_CHECK:-0}" != "1" ]; then
  # 파이프 뒤의 종료 코드는 grep 의 것이라 변수에 받아서 본다.
  if ! GAP_OUT=$(uv run python scripts/build/engine_gap.py --strict 2>&1); then
    echo "$GAP_OUT" | grep -v registry; echo "ERROR: 엔진 갭 🔴 — T232/T233 을 보고 원인을 적은 뒤 SKIP_GAP_CHECK=1 로만 넘긴다"; exit 1
  fi
  echo "$GAP_OUT" | grep -v registry
fi
echo "=== 0b) 자료 출처 키 동기화 (허용 목록 · 서버에 없을 때만 · 값은 안 찍는다)"
# 사용자 2026-09-14 "배포할 때 자동으로". 덮어쓰기 없음 · LIVE_ORDERS·거래소 키는 여전히 사람이.
bash scripts/ops/sync_env_keys.sh

echo "=== 1) 빌드 (app · web — web 은 라벨 탭 없음)"
docker build -q -t "$IMG/app:$TAG" . >/dev/null
docker build -q -t "$IMG/web:$TAG" web >/dev/null

echo "=== 2) 배포 파일 동기화 (compose · scripts/deploy · Makefile)"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" --exclude ".env*" docker/ "$HOST:$REMOTE_DIR/docker/"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" scripts/deploy/ "$HOST:$REMOTE_DIR/scripts/deploy/"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" Makefile "$HOST:$REMOTE_DIR/"

echo "=== 3) 이미지 전송"
docker save "$IMG/app:$TAG" "$IMG/web:$TAG" | gzip -1 | $SSH "gunzip | docker load" | tail -2

echo "=== 4) IMAGE_TAG 갱신 · 블루그린"
$SSH "cd $REMOTE_DIR && sed -i \"s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/\" .env.live && ENV=live IMAGE_TAG=$TAG bash scripts/deploy/bluegreen.sh" 2>&1 | grep -vE "variable is not set|^#[0-9]+ "

echo "=== 5) 태그를 git 에 남긴다 (무엇이 떠 있는지 = 태그)"
git tag "$TAG" >/dev/null && git push -q origin "$TAG" && echo "tag $TAG pushed"

echo "=== 6) 밖에서 확인"
curl -s -o /dev/null -w "https health: %{http_code}\n" --max-time 20 "https://${PUBLIC_DOMAIN:?scripts/ops/host.env 에 PUBLIC_DOMAIN 이 없다}/api/health"
echo "✅ $TAG 배포 완료"

# 🔴 T310 R1(2026-09-26) — 옛 이미지를 남기면 배포마다 약 730 MB 씩 디스크가 찬다(89% 까지 갔다).
#    버전 태그 최신 2개(방금 판 + 되돌림용 하나)와 컨테이너가 쓰는 이미지만 남긴다 · 강제 삭제 없음.
echo "=== 7) 옛 이미지 정리 (최신 2개 · 쓰는 중인 것은 남김)"
$SSH "cd $REMOTE_DIR && KEEP=2 bash scripts/deploy/prune_images.sh" || echo "⚠️ 이미지 정리 실패 — 배포는 끝났다 · 손으로: bash scripts/ops/remote.sh scripts/deploy/prune_images.sh"
