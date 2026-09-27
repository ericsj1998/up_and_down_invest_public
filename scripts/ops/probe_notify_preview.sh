#!/usr/bin/env bash
# 1.21.0 배포 뒤 — 웹 푸시 알림 · 진입 가능성 깜빡임 루프가 조용히 죽지 않았나 (사건 이름 · 개수만 · 값 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_notify_preview.sh
#
# 보는 것: notify_* · preview_* 사건 수(실패 · 느림 포함) · 알림 폴더에 무엇이 있나(파일 이름 · 크기만 — 키 내용은 안 읽는다).
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-30m}"
echo "=== $API · since $SINCE"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"event_type": "(notify_[a-z_]+|preview_[a-z_]+)"' | sort | uniq -c
echo "=== 알림 폴더 (이름 · 크기 · 권한만)"
docker exec "$API" sh -c 'ls -la /app/logs/notify 2>/dev/null || echo "아직 없음 — 처음 켤 때 만든다"' | awk '{print $1, $5, $NF}'
echo "=== 구독 수"
docker exec "$API" sh -c 'for f in /app/logs/notify/subscriptions-*.json; do [ -f "$f" ] && echo "$(basename "$f") $(grep -o endpoint "$f" | wc -l)"; done; true'
