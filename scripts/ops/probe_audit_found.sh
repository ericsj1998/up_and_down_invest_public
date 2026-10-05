#!/usr/bin/env bash
# live_audit_found 사건 내용(읽기 전용 · 코드 · 종목 · 설명만) + 진입 정지 스위치.
#   bash scripts/ops/remote.sh scripts/ops/probe_audit_found.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker logs --since 40m "$API" 2>&1 | grep 'live_audit_found' | grep -oE '"ts": "[^"]+"|"symbol": "[^"]+"|"code": "[^"]+"|"level": "[^"]+"|"detail": "[^"]{0,200}' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 6
echo
echo "== 진입 정지 스위치"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select coalesce(max(value), '(없음)') from app_settings where key='live_entries_halted'"
