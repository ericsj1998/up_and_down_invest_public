#!/usr/bin/env bash
# 빈 볼륨 → 배포 시작 상태 (T216 · 2026-09-04). 호스트에서 실행한다.
#
#   ENV=live ENV_FILE=.env.live scripts/deploy/init_db.sh
#   SKIP_BACKFILL=1 …           백필(오래 걸림)을 건너뛰고 seeded 까지만
#
# 순서와 각 단계 뒤 검사 (하나라도 어기면 멈춘다 — 규칙 #8):
#   1 postgres·redis 기동  →  2 migrate (일회성)  → 검사 migrated
#   3 seed_instruments      → 검사 seeded
#   4 backfill --full       → 검사 ready
#   5 전체 기동
#
# 🔴 개발 pgdata 볼륨을 복사해 오지 않는다. 이 스크립트가 "정리" 대신 "처음부터" 를 택한
#    이유다 — 무엇이 테스트 데이터인지 표마다 다르고, 정리 스크립트는 실수를 부른다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

ENV="${ENV:-dev}"
ENV_FILE="${ENV_FILE:-.env.$ENV}"
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE 가 없다"; exit 1; }
C="docker compose --env-file $ENV_FILE -f docker/compose.base.yml -f docker/compose.$ENV.yml"
RUN="$C run --rm --no-deps api"

echo "=== 1) postgres · redis ==="
$C up -d --wait postgres redis

echo "=== 2) migrate (alembic upgrade head · 일회성) ==="
$C run --rm --no-deps migrate
$RUN python scripts/deploy/verify_clean_db.py --stage migrated

echo "=== 3) instruments 시드 (config/backfill.yml universe) ==="
$RUN python scripts/runtime/seed_instruments.py
$RUN python scripts/deploy/verify_clean_db.py --stage seeded

if [ "${SKIP_BACKFILL:-0}" = "1" ]; then
  echo "=== 4) 백필 건너뜀 (SKIP_BACKFILL=1) — 'ready' 검사는 백필 뒤에 따로 돌려라 ==="
else
  echo "=== 4) 캔들 백필 (고정 앵커 · 유니버스 x 시간축) — 오래 걸린다 ==="
  $RUN python scripts/runtime/backfill_cli.py --full
  $RUN python scripts/deploy/verify_clean_db.py --stage ready
fi

echo "=== 5) 전체 기동 ==="
$C up -d --wait
$C ps --format "table {{.Service}}\t{{.Status}}"
echo "✅ init_db 완료 — 첫 로그인은 ADMIN_EMAILS 계정으로 (docs/platform/deploy.md §5)"
