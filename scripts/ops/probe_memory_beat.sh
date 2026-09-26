#!/usr/bin/env bash
# api 메모리 추세 — `api_memory_beat`(기동 2분 뒤 한 번 · 이후 30분마다)를 시간순으로 (T310 R4 · 값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_memory_beat.sh
#
# 보는 것: RSS · 스왑이 가동 시간에 따라 자라는가 · 판별 급전 봉(1h · 4h · 1d · 가격축)이 같이 자라는가.
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · 시작 $(docker inspect -f '{{.State.StartedAt}}' "$API" | cut -c1-19) · 지금 $(date -u +%m-%dT%H:%M:%S)"
echo "시각 | rss MB | swap MB | 판 | 급전 봉(시간축별) | 봉 많은 판"
docker logs --timestamps "$API" 2>&1 | grep '"event_type": "api_memory_beat"' | while read -r line; do
  t=$(echo "$line" | cut -c1-16)
  rss=$(echo "$line" | grep -oE '"rss_mb": [0-9.]+' | grep -oE '[0-9.]+$')
  swap=$(echo "$line" | grep -oE '"swap_mb": [0-9.]+' | grep -oE '[0-9.]+$')
  n=$(echo "$line" | grep -oE '"sessions": [0-9]+' | grep -oE '[0-9]+$')
  bars=$(echo "$line" | grep -oE '"feed_bars_total": \{[^}]*\}' | cut -d'{' -f2 | tr -d '"}')
  top=$(echo "$line" | grep -oE '"symbol": "[A-Z_]+", "bars": [0-9]+' | head -3 | sed 's/"symbol": "//; s/", "bars": /=/' | tr '\n' ' ')
  echo "$t | $rss | $swap | $n | $bars | $top"
done | tail -n 60
echo "=== 지금 free -m"
free -m | head -3
