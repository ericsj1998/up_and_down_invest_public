#!/usr/bin/env bash
# 계좌 총액 스냅샷 — 어느 컨테이너가 어디에 적었나 (10,079 USDT 가 실계좌 300 과 다르다 · 2026-09-08)
set -u
for c in updown_live-api-1 updown_live-api_b-1 updown_live-api_demo-1; do
  echo "=== $c"
  docker exec "$c" sh -c 'for f in $(find / -path /proc -prune -o -name daily.jsonl -print 2>/dev/null); do echo "--- $f"; tail -3 "$f" | cut -c1-200; done' 2>&1 | head -12
  docker inspect "$c" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' 2>/dev/null | grep -v "^$" | head -6
done
echo "=== equity-snapshot 로그 (48h)"
for c in updown_live-api-1 updown_live-api_b-1 updown_live-api_demo-1; do
  echo "--- $c"; docker logs --since 48h "$c" 2>&1 | grep -oE '"event_type": "equity_snapshot[a-z_]*"[^}]{0,160}' | head -3
done
