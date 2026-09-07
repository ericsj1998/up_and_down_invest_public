#!/bin/bash
# 복구 — 호스트에서 실행한다 (T213 · 2026-09-04).
#
#   docker/restore.sh <dump 파일 이름> [대상 DB 이름]
#
#   대상 DB 를 생략하면 **운영 DB 에 덮어쓴다** (`pg_restore --clean`). 연습·검증에는 반드시
#   두 번째 인자로 딴 이름을 줘라 — 그러면 그 이름으로 새 DB 를 만들어 거기에 푼다.
#
# 절차와 연습 기록: docs/platform/deploy.md §9
set -eu

DUMP="${1:?dump 파일 이름이 필요하다 (backups 볼륨 안 · 예: updown-20260904T0600Z.dump)}"
TARGET="${2:-}"
ENV_FILE="${ENV_FILE:-.env.dev}"
ENV="${ENV:-dev}"

set -a; . "./$ENV_FILE"; set +a
COMPOSE="docker compose --env-file $ENV_FILE -f docker/compose.base.yml -f docker/compose.$ENV.yml"

if [ -z "$TARGET" ]; then
  echo "🔴 운영 DB($POSTGRES_DB)에 덮어쓴다. api·engine 을 먼저 내린다."
  $COMPOSE stop api engine
  TARGET="$POSTGRES_DB"
else
  echo "연습 복구 → 새 DB '$TARGET'"
  $COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET\";" -c "CREATE DATABASE \"$TARGET\";"
fi

# backups 볼륨은 backup 서비스에 붙어 있다 — 거기서 파일을 postgres 로 넘긴다.
# 🔴 `--entrypoint` 를 반드시 준다. 서비스 엔트리포인트가 `backup.sh` 라 그냥 `run ... sh -c`
#    하면 그 인자가 backup.sh 로 들어가 **백업 루프**가 돈다 (2026-09-04 검증에서 10분 걸림).
$COMPOSE run --rm --no-deps -T --entrypoint sh backup -c "cat /backups/$DUMP" \
  | $COMPOSE exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$TARGET" --clean --if-exists --no-owner --no-privileges

echo "복구 완료 → $TARGET"
$COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d "$TARGET" -Atc \
  "select 'wf_runs '||count(*) from wf_runs union all select 'candles '||count(*) from candles union all select 'users '||count(*) from users;"

if [ "$TARGET" = "$POSTGRES_DB" ]; then
  $COMPOSE up -d --wait api engine
fi
