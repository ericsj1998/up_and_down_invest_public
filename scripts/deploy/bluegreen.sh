#!/usr/bin/env bash
# 블루그린 재배포 — RUN 을 끊지 않고 api 를 새 이미지로 (T212 · 2026-09-04). 호스트에서.
#
#   ENV=live ENV_FILE=.env.live scripts/deploy/bluegreen.sh
#
# 어떻게 끊기지 않나:
#   RUN 세션은 api 프로세스 안에 산다. 그래서 "프로세스가 안 죽는다" 가 아니라 **"거래
#   리더가 끊기지 않는다"** 가 목표다. api 슬롯이 둘(api · api_b)이고 트레이더 락
#   (`updown:api:trader`)이 리더를 정한다:
#
#     1 새 이미지 빌드
#     2 비어 있는 슬롯을 새 이미지로 기동 → healthy · 팔로워(trading_leader=false) 확인
#     3 옛 슬롯 stop (grace 30s: 거래 루프 거두고 락 놓음)
#     4 새 슬롯이 승격(trading_leader=true) → RUN 입양 (autostart_live · adopt)
#     5 engine 을 새 이미지로 재생성 (락 이양 · grace 60s) · web 갱신
#   어느 단계든 실패하면 새 슬롯을 내리고 옛 슬롯을 그대로 둔다.
#
# 🔴 마이그레이션은 **하위 호환**이어야 한다 — 2·3 사이에 두 이미지가 같은 DB 를 쓴다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

ENV="${ENV:-dev}"
ENV_FILE="${ENV_FILE:-.env.$ENV}"
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE 가 없다"; exit 1; }
C="docker compose --env-file $ENV_FILE -f docker/compose.base.yml -f docker/compose.$ENV.yml --profile bluegreen"
# 라이브는 Caddy(compose.proxy.yml)가 같은 프로젝트에 있다 — 여기 목록에 없다고 "고아" 경고를 내며 건드리지 않게
export COMPOSE_IGNORE_ORPHANS=1
# 리허설 훅 — 되돌림 경로를 강제로 밟아 볼 때 override 파일을 하나 더 얹는다 (운영에선 비워 둔다)
[ -n "${EXTRA_COMPOSE:-}" ] && C="$C -f $EXTRA_COMPOSE"
PROJECT="$($C config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"

running() { docker ps --filter "name=^${PROJECT}-$1-1$" --filter status=running -q | grep -q . ; }
health() {  # <slot> → JSON of /health (컨테이너 안에서)
  docker exec "${PROJECT}-$1-1" python -c "import urllib.request,sys;sys.stdout.write(urllib.request.urlopen('http://localhost:8000/health',timeout=3).read().decode())" 2>/dev/null || echo "{}"
}
leader_of() { health "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("trading_leader"))' 2>/dev/null || echo "?"; }
wait_for() {  # <slot> <expected leader: True|False> <seconds>
  local slot="$1" want="$2" secs="$3" i=0
  while [ $i -lt "$secs" ]; do
    if [ "$(leader_of "$slot")" = "$want" ]; then return 0; fi
    sleep 1; i=$((i+1))
  done
  return 1
}

if running api && running api_b; then
  echo "ERROR: api 와 api_b 가 둘 다 떠 있다 — 지난 배포가 끝나지 않았다. 리더가 아닌 쪽을 먼저 내려라"; exit 1
elif running api;   then OLD=api;   NEW=api_b
elif running api_b; then OLD=api_b; NEW=api
else
  echo "ERROR: 도는 api 슬롯이 없다 — 첫 기동은 make up 이다"; exit 1
fi
echo "=== 블루그린: $OLD → $NEW  (project $PROJECT · env $ENV) ==="
echo "옛 슬롯 리더? $(leader_of "$OLD")"

if [ -n "${IMAGE_TAG:-}" ]; then
  # 라이브: 서버는 빌드하지 않는다 (1 GB). 이미지가 이미 실려 와 있으면(`ship.sh` 의 save|load) 그걸 쓰고,
  # 없을 때만 레지스트리에서 받는다 (release-images · GHCR 로그인 필요).
  IMG="ghcr.io/ericsj1998/up_and_down_invest"
  if docker image inspect "$IMG/app:$IMAGE_TAG" "$IMG/web:$IMAGE_TAG" >/dev/null 2>&1; then
    echo "=== 1) 이미지 이미 있음 (IMAGE_TAG=$IMAGE_TAG · pull 생략) ==="
  else
    echo "=== 1) 이미지 pull (IMAGE_TAG=$IMAGE_TAG) ==="
    $C pull -q api web
  fi
else
  echo "=== 1) 빌드 ==="
  $C build api web engine 2>&1 | grep -iE "error|naming to" | tail -3
fi

echo "=== 1b) 마이그레이션 (alembic upgrade head · $ENV DB) ==="
# 🔴 새 슬롯은 `--no-deps` 로 올리므로 compose 의 `depends_on: migrate` 가 **돌지 않는다** — 2026-09-06 실측:
#    0108(accounts.audit)이 라이브 DB 에 안 들어가 구글 로그인 콜백이 500 을 냈다. 데모(4b)와 같은 모양으로
#    새 슬롯보다 먼저 돌리고, 실패하면 옛 슬롯을 건드리지 않고 멈춘다 (규칙 #8 — 옛 스키마에 붙어 런타임에 터지지 않게).
if $C up --no-deps --exit-code-from migrate migrate > /tmp/bluegreen_migrate.log 2>&1; then migrate_rc=0; else migrate_rc=$?; fi
grep -vE "variable is not set|^#[0-9]+ " /tmp/bluegreen_migrate.log | tail -4
if [ "$migrate_rc" != "0" ]; then
  echo "🔴 마이그레이션이 실패했다 (exit $migrate_rc) — 새 슬롯을 올리지 않는다. 옛 슬롯 $OLD 는 그대로다"
  exit 1
fi

echo "=== 2) $NEW 기동 (새 이미지 · 팔로워로) ==="
# `up --wait` 는 새 슬롯이 unhealthy 로 판정되면 비영(非零)으로 끝난다 — set -e 에 맡기면
# 메시지 없이 죽고 재시작 루프가 남는다 (2026-09-04 강제 실패 리허설에서 확인). 그래서 if 로 받는다.
if ! $C up -d --no-deps --wait "$NEW" || ! wait_for "$NEW" False 30; then
  echo "🔴 $NEW 가 healthy 팔로워로 안 올라왔다 ($(leader_of "$NEW")) — 되돌린다. 옛 슬롯 $OLD 는 건드리지 않았다"
  docker logs --tail 5 "${PROJECT}-$NEW-1" 2>&1 | cut -c1-200
  $C stop "$NEW"; exit 1
fi
echo "   $NEW 팔로워 확인"

echo "=== 3) $OLD stop (락 놓음 · grace 30s) ==="
$C stop "$OLD"

echo "=== 4) $NEW 승격 대기 ==="
if ! wait_for "$NEW" True 60; then
  echo "🔴 $NEW 가 60s 안에 승격되지 않았다 ($(leader_of "$NEW")) — 옛 슬롯을 다시 띄운다"
  $C up -d --no-deps "$OLD"; $C stop "$NEW"; exit 1
fi
echo "   $NEW 리더 승격 · RUN 입양 중"
sleep 5
docker logs --since 90s "${PROJECT}-$NEW-1" 2>&1 | grep -oE '"event_type": "(trader_promoted|live_run_resumed|funds_restored|orphan[a-z_]*)"' | sort | uniq -c

echo "=== 4b) 데모 API (T221 · #19) ==="
# 데모 API 는 슬롯이 없다 — 게스트 세션은 잃어도 된다. `.env.demo` 가 없으면 건너뛰고 경고한다(실계좌 배포를 막지 않는다).
# 순서: migrate_demo(일회성 · 데모 DB 생성 + alembic) → api_demo 재생성. `--no-deps` 라 의존은 여기서 손으로 잇는다.
# 🔴 서비스 목록은 **변수로 먼저 받는다** (1.16.0 · 2026-09-24). `config --services | grep -q` 는 pipefail 아래에서
#    grep 이 먼저 끝나면 compose 가 SIGPIPE 로 비영 종료해 조건이 거짓이 된다 — 데모가 "이 환경엔 없다" 로 건너뛰어
#    옛 이미지(v1.15.1)에 남았다(1.15.1 땐 경합에서 이겼을 뿐).
SERVICES="$($C config --services 2>/dev/null || true)"
if grep -qx api_demo <<<"$SERVICES"; then
  if [ -f "$(dirname "$ENV_FILE")/.env.demo" ]; then
    if $C up --no-deps --exit-code-from migrate_demo migrate_demo 2>&1 | grep -iE "error|traceback|exited with code" | tail -3; then :; fi
    if $C up -d --no-deps --wait api_demo; then
      echo "   api_demo 갱신 ($(docker ps --filter "name=^${PROJECT}-api_demo-1$" --format '{{.Status}}'))"
    else
      echo "⚠️ api_demo 가 healthy 로 안 올라왔다 — 실계좌는 영향 없음. docker logs ${PROJECT}-api_demo-1 를 본다"
      docker logs --tail 5 "${PROJECT}-api_demo-1" 2>&1 | cut -c1-200
    fi
  else
    echo "⚠️ .env.demo 가 없다 — 데모 API 를 올리지 않는다 (docs/platform/env_live.md · env_sync.sh)"
  fi
else
  echo "   api_demo: 이 환경엔 없다"
fi

echo "=== 5) engine 재생성 · web 갱신 (이미지가 바뀐 경우만 재생성) ==="
# live 는 engine 이 프로필 뒤에 있다 (api 안에서 돈다 · compose.live.yml). 활성 서비스에
# 없으면 건드리지 않는다 — 이름을 지정해 up 하면 프로필이 저절로 켜져 두 벌이 돌게 된다.
if grep -qx engine <<<"$SERVICES"; then
  $C up -d --no-deps --wait engine
else
  echo "   engine: 별도 컨테이너 없음 (리더 api 안에서 돈다)"
fi
# ⚠️ web 을 강제로 재시작하지 않는다 — nginx 가 `resolve` 로 api 주소를 5초마다 다시 푸니
#    api 재생성에 web 재시작이 필요하던 옛 문제("api 재생성 뒤 web 재시작" 메모)가 없다.
#    첫 리허설(2026-09-04)에서 강제 재시작이 health 실패 10회 중 대부분을 만들었다.
$C up -d --no-deps web
$C ps --format "table {{.Service}}\t{{.Status}}"
echo "✅ 배포 완료 — 리더 = $NEW. 다음 배포는 $NEW → $OLD 로 간다"
