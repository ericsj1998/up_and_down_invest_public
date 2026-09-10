#!/usr/bin/env bash
# 연구 PC DB 의 EDGAR 사실(`financial_facts` · 공개 자료 · 시크릿 없음)을 실계좌 DB 로 복사한다 — 저평가 후보 2단계(이력)를
# 서버에서 즉시 보이게 (사용자 2026-09-11 "이력이 하나도 안 잡히네"). 서버는 EDGAR 를 종목마다 새로 받는 대신 표를 옮긴다.
#
#   bash scripts/ops/copy_facts_to_live.sh            # 실계좌 DB(updown) 로
#   bash scripts/ops/copy_facts_to_live.sh demo       # 데모 DB(updown_demo) 로
#
# - 로컬: `docker exec updown-postgres-1 pg_dump --data-only -t financial_facts` (INSERT · ON CONFLICT DO NOTHING 로 바꿔 보냄)
# - 서버: `psql` 로 넣는다. 이미 있는 행은 건드리지 않는다(같은 키면 건너뜀). 원본 표는 지우지 않는다.
# ⚠️ 실계좌 DB 에 쓰는 일이다 — 사람이 돌린다. 값·주소는 찍지 않는다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
TARGET_DB="updown"; [ "${1:-}" = "demo" ] && TARGET_DB="updown_demo"
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
echo "== 로컬 financial_facts 덤프"
docker exec updown-postgres-1 pg_dump -U updown -d updown --data-only --inserts --on-conflict-do-nothing -t financial_facts > "$tmp"
echo "   $(grep -c '^INSERT' "$tmp") 행 · $(du -h "$tmp" | cut -f1)"
gzip -f "$tmp"
scp -q -i "$KEY" "$tmp.gz" "$HOST:/tmp/financial_facts.sql.gz"
ssh -i "$KEY" -o BatchMode=yes "$HOST" "TARGET_DB='$TARGET_DB' bash -s" <<'REMOTE'
set -euo pipefail
cd ~/updown
before=$(docker exec updown_live-postgres-1 psql -U updown -d "$TARGET_DB" -At -c "select count(*) from financial_facts")
gunzip -c /tmp/financial_facts.sql.gz | docker exec -i updown_live-postgres-1 psql -U updown -d "$TARGET_DB" -q -v ON_ERROR_STOP=1
after=$(docker exec updown_live-postgres-1 psql -U updown -d "$TARGET_DB" -At -c "select count(*), count(distinct symbol) from financial_facts")
echo "   $TARGET_DB financial_facts: $before → $after (행 · 종목)"
rm -f /tmp/financial_facts.sql.gz
REMOTE
rm -f "$tmp.gz"
echo "== 끝. 저평가 카드의 '전체' 탭을 새로고침하면 2단계(5년 백분위 · 점수)가 뜬다 — 캐시 TTL 뒤."
