#!/usr/bin/env bash
# 리더를 잃고 재시도 루프가 안 생긴 실계좌 API 를 다시 띄우고 리더 · healthy 까지 기다린다 (2026-09-29 · 사용자 "필요하면 조치").
#   bash scripts/ops/remote.sh scripts/ops/restart_api_b_wait.sh
# 전제: 거래소 포지션 · 주문 0 · 스틸 낮음 · 펀드 전환 끝남. 데모 API 는 안 올린다.
API=$(docker ps -a --format '{{.Names}}' | grep -E 'updown_live-api(_b)?-1' | while read -r c; do
  if [ "$(docker inspect --format '{{.State.Running}}' "$c")" = "true" ]; then echo "$c"; fi; done | head -1)
[ -n "$API" ] || { echo "🔴 도는 실계좌 API 컨테이너가 없다"; docker ps -a --format '{{.Names}} {{.Status}}' | grep api; exit 1; }
health() { docker exec "$API" python -c "import urllib.request,json;h=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=8));print(h.get('trading_leader'))" 2>/dev/null || echo "?"; }
echo "target: $API · before: leader=$(health) · $(date -u +%H:%M:%SZ)"
docker restart -t 30 "$API" >/dev/null && echo "restarted · $(date -u +%H:%M:%SZ)"
for i in $(seq 1 90); do
  sleep 10
  l=$(health)
  if [ "$l" = "True" ]; then echo "leader=True · $(date -u +%H:%M:%SZ) · $((i*10))s"; break; fi
done
echo "status: $(docker ps --format '{{.Names}} {{.Status}}' | grep "$API")"
echo "steal: $(top -bn1 | sed -n 3p | grep -oE '[0-9.]+ st')"
docker logs --tail 6000 "$API" 2>&1 | grep -oE '"event_type": "(api_started|trader_leader|trader_promoted|engine_lock_acquired|live_run_resumed|fund_gate_attached|fund_members_released|fund_restored)"' | sort | uniq -c
docker exec updown_live-web-1 sh -c 'wget -q -O- -T 8 --header="Cookie: updown_mode=live" http://127.0.0.1/api/health 2>&1 | head -c 80; echo'
