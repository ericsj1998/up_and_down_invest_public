#!/usr/bin/env bash
# 먼저 멈추고 바꾸기 — 블루그린이 메모리 때문에 두 슬롯을 못 겹칠 때 (2026-09-26 · v1.18.1).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_gate.py          # 먼저: POSITIONS 0 · OPEN_ORDERS 0 · STOP_ORDERS 0 이어야 한다
#   bash scripts/ops/remote.sh scripts/ops/swap_stop_first.sh
#
# 왜: 서버는 1 GB 다. 실계좌 api 가 약 340 MB 를 쓰고 스왑이 700 MB 차 있는 상태에서 새 슬롯이 40판을 되살리면
# 6분 넘게 걸려 헬스체크(start_period 240s · 1분 x 3)에 못 들고, 그 사이 옛 슬롯이 트레이더 락 갱신을 놓쳐 새 슬롯이
# 리더를 가져갔다 — 블루그린이 되돌렸다(v1.18.1 첫 시도). 여기서는 옛 슬롯을 **먼저** 내리고(락 놓음) 새 슬롯 하나만 올린다.
#
# 🔴 쓰는 조건: 거래소 포지션 · 대기 주문 · 손절 주문이 **0** 일 때만. 내려가 있는 몇 분 동안 아무도 손절을 관리하지 않는다.
# 순서: 옛 슬롯 stop(grace 30s) → 새 슬롯 up(IMAGE_TAG = .env.live 값) → 리더 승격 대기(최대 20분) → healthy 대기 →
#       데모 · engine · web(블루그린 4b · 5 와 같다). 승격이 안 되면 새 슬롯을 내리고 옛 슬롯을 다시 올린다.
# 마이그레이션은 이 스크립트가 안 돈다 — 블루그린 1b 가 이미 돌았거나 새 revision 이 없을 때만 쓴다.
set -uo pipefail
cd ~/updown || exit 1
ENV=live
ENV_FILE=.env.live
C="docker compose --env-file $ENV_FILE -f docker/compose.base.yml -f docker/compose.$ENV.yml --profile bluegreen"
export COMPOSE_IGNORE_ORPHANS=1
PROJECT="$($C config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
TAG=$(grep -E '^IMAGE_TAG=' "$ENV_FILE" | cut -d= -f2-)

running() { docker ps --filter "name=^${PROJECT}-$1-1$" --filter status=running -q | grep -q . ; }
health() {
  docker exec "${PROJECT}-$1-1" python -c "import urllib.request,sys;sys.stdout.write(urllib.request.urlopen('http://localhost:8000/health',timeout=10).read().decode())" 2>/dev/null || echo "{}"
}
leader_of() { health "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("trading_leader"))' 2>/dev/null || echo "?"; }

if running api && running api_b; then
  echo "ERROR: api 와 api_b 가 둘 다 떠 있다 — 리더가 아닌 쪽을 먼저 내려라"; exit 1
elif running api;   then OLD=api;   NEW=api_b
elif running api_b; then OLD=api_b; NEW=api
else
  echo "ERROR: 도는 api 슬롯이 없다"; exit 1
fi
echo "=== 멈추고 바꾸기: $OLD → $NEW · IMAGE_TAG=$TAG · project $PROJECT ==="
docker image inspect "ghcr.io/ericsj1998/up_and_down_invest/app:$TAG" >/dev/null 2>&1 || { echo "🔴 이미지 app:$TAG 가 서버에 없다 — ship.sh 로 먼저 싣는다"; exit 1; }
free -m | head -2

echo "=== 1) $OLD stop (락 놓음 · grace 30s) · $(date -u +%H:%M:%S)"
$C stop "$OLD"

echo "=== 2) $NEW 기동 (새 이미지 · 기다리지 않음) · $(date -u +%H:%M:%S)"
$C up -d --no-deps "$NEW"

echo "=== 3) $NEW 리더 승격 대기 (최대 1200s)"
ok=0
for i in $(seq 1 120); do
  if [ "$(leader_of "$NEW")" = "True" ]; then ok=1; break; fi
  sleep 10
done
if [ "$ok" != "1" ]; then
  echo "🔴 $NEW 가 20분 안에 리더가 안 됐다 ($(leader_of "$NEW")) — 되돌린다 · $(date -u +%H:%M:%S)"
  docker logs --tail 5 "${PROJECT}-$NEW-1" 2>&1 | cut -c1-200
  $C stop "$NEW"; $C up -d --no-deps "$OLD"; exit 1
fi
echo "   $NEW 리더 · $(date -u +%H:%M:%S)"

echo "=== 4) healthy 대기 (최대 900s)"
for i in $(seq 1 90); do
  st=$(docker inspect -f '{{.State.Health.Status}}' "${PROJECT}-$NEW-1" 2>/dev/null)
  [ "$st" = "healthy" ] && break
  sleep 10
done
echo "   상태 $(docker inspect -f '{{.State.Health.Status}}' "${PROJECT}-$NEW-1") · $(date -u +%H:%M:%S)"
docker logs --since 20m "${PROJECT}-$NEW-1" 2>&1 | grep -oE '"event_type": "(api_started|trader_promoted|live_run_resumed|funds_restored|fund_gate_attached|fund_members_released|live_underfunded|orphan[a-z_]*)"' | sort | uniq -c

echo "=== 5) 데모 API (블루그린 4b 와 같다)"
SERVICES="$($C config --services 2>/dev/null || true)"
if grep -qx api_demo <<<"$SERVICES" && [ -f .env.demo ]; then
  $C up --no-deps --exit-code-from migrate_demo migrate_demo 2>&1 | grep -iE "error|traceback|exited with code" | tail -3
  $C up -d --no-deps --wait api_demo && echo "   api_demo 갱신" || echo "⚠️ api_demo 가 healthy 로 안 올라왔다 — 실계좌 영향 없음"
fi

echo "=== 6) engine · web (블루그린 5 와 같다)"
if grep -qx engine <<<"$SERVICES"; then $C up -d --no-deps --wait engine; else echo "   engine: 별도 컨테이너 없음"; fi
$C up -d --no-deps web
$C ps --format "table {{.Service}}\t{{.Status}}"
echo "✅ 완료 — 리더 = $NEW · $(date -u +%H:%M:%S)"
