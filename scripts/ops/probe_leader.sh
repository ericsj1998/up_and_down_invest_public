#!/usr/bin/env bash
# 리더가 있나 — Redis 락 키들 · API 리더 이벤트 · /health 의 leader 칸 (읽기 전용 · 2026-09-29).
echo "== redis keys"; docker exec updown_live-redis-1 redis-cli --scan --pattern 'updown:*' | head -20
for k in $(docker exec updown_live-redis-1 redis-cli --scan --pattern 'updown:*lock*' ; docker exec updown_live-redis-1 redis-cli --scan --pattern 'updown:*trader*' ; docker exec updown_live-redis-1 redis-cli --scan --pattern 'updown:*leader*'); do
  echo "  $k ttl=$(docker exec updown_live-redis-1 redis-cli ttl "$k") val=$(docker exec updown_live-redis-1 redis-cli get "$k" | cut -c1-12)…"
done
echo "== api_b 리더 이벤트 (전체 tail 40000)"; docker logs --tail 40000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "[a-z_]*(lock|leader)[a-z_]*"' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"' | paste - - | tail -12
echo "== api 안 health"; docker exec updown_live-api_b-1 python -c "import urllib.request,json;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=20)))[:600])" 2>&1 | tail -2
