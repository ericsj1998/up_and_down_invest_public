#!/usr/bin/env bash
# T332 자동 대체 확인 (읽기 전용) — 예약 · 전환 사건 · 펀드 파일의 매매법 · 다리 수 · 판의 매매법 세트.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) 리더 후보 $API"
echo "== 대체 사건(전부)"
docker logs "$API" 2>&1 | grep -E 'fund_supersede_scheduled|fund_playbook_superseding|fund_playbook_superseded|fund_playbook_supersede_failed|fund_rules_switched|fund_members_widened|funds_restored|fund_restore_failed|funds_pending' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|fund_supersede_scheduled: [0-9.]+s|funds_restored: [0-9]+|"from": "[^"]+"|"to": "[^"]+"|"members": [0-9]+|"error": "[^"]{0,160}' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 12
echo "== 펀드 파일(매매법 · 다리 수 · legs_revision)"
for f in $(docker exec "$API" sh -c 'ls ${FUNDS_ROOT:-logs/funds}/*.json 2>/dev/null'); do
  docker exec "$API" python3 -c "import json,sys; d=json.load(open('$f')); print('$f', d.get('playbook'), 'legs', len(d.get('legs') or []), 'rev', d.get('legs_revision'), [l.get('playbook') for l in (d.get('legs') or [])])"
done
echo "== 판 매매법 세트(DB · 열린 판 · 종류별 수)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select playbook, count(*) from wf_runs where closed_at is null group by playbook order by 2 desc" 2>/dev/null | head -n 6
echo "== 최근 30분 경고 · 오류"
docker logs --since 30m "$API" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -n 8
