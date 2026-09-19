#!/usr/bin/env bash
# T288 배포 뒤 점검 — `wf_trades.filled_leverage` 열이 실제로 붙었나.
#
# 열이 없으면 러너가 **원장 저장에서** 터진다(진입은 이미 나간 뒤다). 배포 로그의
# "완료" 는 컨테이너가 떴다는 뜻이지 마이그레이션이 돌았다는 뜻이 아니라서 따로 본다.
set -uo pipefail
PG=updown_live-postgres-1

echo "=== alembic head (DB) ==="
docker exec "$PG" psql -U updown -d updown -tAc "select version_num from alembic_version;"

echo "=== wf_trades 의 노출 관련 열 ==="
docker exec "$PG" psql -U updown -d updown -tAc \
  "select column_name || ' · ' || data_type || ' · nullable=' || is_nullable
     from information_schema.columns
    where table_name='wf_trades' and column_name in ('filled_leverage','leverage')
    order by column_name;"

echo "=== 값이 붙은 기록 ==="
docker exec "$PG" psql -U updown -d updown -tAc \
  "select '붙음 ' || count(*) filter (where filled_leverage is not null) || ' / 전체 ' || count(*)
     from wf_trades;"

echo "=== api 상태 ==="
docker ps --filter name=api --format '{{.Names}} {{.Status}}'
