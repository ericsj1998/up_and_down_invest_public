#!/usr/bin/env bash
# stop-first 교체 직후 — 컨테이너 건강 · 메모리 · 스틸 · API 안쪽 헬스 · 마지막 로그 (읽기 전용 · 2026-09-29 1.26.0).
#   bash scripts/ops/remote.sh scripts/ops/probe_after_swap.sh
echo "== containers"; docker ps --format '{{.Names}}\t{{.Status}}'
echo "== mem"; free -m | head -2
echo "== steal (top 1줄)"; top -bn1 | sed -n 3p
echo "== load"; cat /proc/loadavg
for c in updown_live-api_b-1 updown_live-api_demo-1; do
  echo "== $c health log (마지막 3)"; docker inspect --format '{{range .State.Health.Log}}{{.Start}} {{.ExitCode}} {{.Output}}{{"\n"}}{{end}}' "$c" | tail -3 | cut -c1-240
  echo "== $c 마지막 로그 40줄 중 경고·에러"; docker logs --tail 400 "$c" 2>&1 | grep -E '"level": "(warning|error)"|Traceback|Error' | tail -8 | cut -c1-240
done
echo "== web 마지막 5"; docker logs --tail 5 updown_live-web-1 2>&1 | cut -c1-200
