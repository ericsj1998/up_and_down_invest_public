#!/usr/bin/env bash
# 배포 뒤 펀드 되살리기 사건 (읽기 전용 · 15분) — 펀드 · 다리 · 되살림 · 갱신 · 오류 사건과 요약.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%MZ) $API $(docker inspect -f '{{.Config.Image}}' "$API" | sed 's/.*://')"
docker logs --since 15m "$API" 2>&1 | grep -oE '"event_type": "(fund|funds|live_run|trader|rebal)[a-z_]*' | sort | uniq -c
echo "== 펀드 · 다리 줄"
docker logs --since 15m "$API" 2>&1 | grep -E 'fund_legs|funds_restored|fund_restore|legs_revision' | grep -oE '"ts": "[^"]{19}|"event_type": "[a-z_]+"|"(from|to|fund_id|reason|error|note)": "?[^",}]{0,120}' | paste -sd' ' | fold -w 250 | head -n 10
echo "== 선언(이미지 안 config) legs_revision · 돌파 롱 노출"
docker exec "$API" sh -c "grep -n -A2 '^  private_strategy:' config/playbooks.yml | head -3; grep -n 'legs_revision: 2' config/playbooks.yml | head -2; grep -n 'leg_exposure: \"2.2\"' config/playbooks.yml | head -1"
