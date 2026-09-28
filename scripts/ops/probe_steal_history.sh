#!/usr/bin/env bash
# 스틸이 언제부터인가 — 컨테이너별 CPU 지금 값 · API 로그의 "job missed" 경고를 시간대별로 센다(스틸의 간접 지표 · 읽기 전용 · 2026-09-29).
#   bash scripts/ops/remote.sh scripts/ops/probe_steal_history.sh
echo "== uptime"; uptime
echo "== docker stats (한 번)"; timeout 60 docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>&1
for c in updown_live-api-1 updown_live-api_b-1 updown_live-orderflow-1; do
  echo "== $c · 시간대별 'was missed' 경고 수 (tail 150000)"
  docker logs --tail 150000 "$c" 2>&1 | grep -oE '"ts": "2026-09-2[0-9]T[0-9]{2}' | sort | uniq -c | awk '{printf "%s %s\n",$2,$1}' | tail -30 > /tmp/all_hours.txt
  docker logs --tail 150000 "$c" 2>&1 | grep "was missed" | grep -oE '"ts": "2026-09-2[0-9]T[0-9]{2}' | sort | uniq -c | awk '{printf "%s missed=%s\n",$2,$1}' | tail -30
done
echo "== orderflow 마지막 5줄"; docker logs --tail 5 updown_live-orderflow-1 2>&1 | cut -c1-220
