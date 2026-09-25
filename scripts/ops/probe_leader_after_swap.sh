#!/usr/bin/env bash
# 슬롯 바꾼 뒤 리더 · 락 사건 시각 · 계좌 예산 검사 사건 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_leader_after_swap.sh
for C in updown_live-api-1 updown_live-api_b-1; do
  st=$(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$C" 2>/dev/null | cut -c1-30)
  echo "=== $C · $st"
  if [ "${st%% *}" = "running" ]; then
    docker exec "$C" python -c "import urllib.request,json;h=json.load(urllib.request.urlopen('http://localhost:8000/health',timeout=10));print('trading_leader=',h.get('trading_leader'),'· version=',h.get('version'))" 2>&1 | tail -1
  fi
  docker logs --timestamps --since 40m "$C" 2>&1 | grep -E 'trader_lock_lost|engine_lock_lost|trader_promoted|trader_follower|live_underfunded|live_funded_again' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"' | paste - - | tail -8
done
