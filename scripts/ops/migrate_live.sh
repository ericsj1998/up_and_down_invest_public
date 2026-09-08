#!/usr/bin/env bash
# 라이브 DB alembic upgrade head — 서버에서 (시크릿 없음 · 출력은 alembic 줄과 종료 코드뿐).
#
#   bash scripts/ops/remote.sh scripts/ops/migrate_live.sh
#
# 왜: 2026-09-06 블루그린이 새 슬롯을 --no-deps 로 올려 compose 의 depends_on(migrate) 이 안 돌았고,
#     0108(accounts.audit)이 빠져 구글 로그인 콜백이 500 을 냈다. 1.0.4 부터는 bluegreen.sh 가 먼저 돌린다.
set -u
cd ~/updown || exit 1
C="docker compose --env-file .env.live -f docker/compose.base.yml -f docker/compose.live.yml --profile bluegreen"
echo "=== IMAGE_TAG: $(grep -E '^IMAGE_TAG=' .env.live | cut -d= -f2)"
echo "=== migrate (live DB)"
if $C up --no-deps --exit-code-from migrate migrate > /tmp/migrate_live.log 2>&1; then rc=0; else rc=$?; fi
grep -vE "variable is not set|^#[0-9]+ " /tmp/migrate_live.log | tail -12
echo "=== exit $rc"
rm -f /tmp/migrate_live.log
exit $rc
