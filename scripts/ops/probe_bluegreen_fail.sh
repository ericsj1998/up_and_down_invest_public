#!/usr/bin/env bash
# 블루그린 롤백 원인 — 새 슬롯(api_b)이 기동을 끝냈나 · 메모리로 죽었나 · 헬스체크 기록 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_bluegreen_fail.sh
C=updown_live-api_b-1
echo "=== 상태"
docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}' "$C" 2>&1 | cut -c1-200
echo "=== 헬스체크 설정 · 마지막 기록"
docker inspect -f '{{json .Config.Healthcheck}}' "$C" 2>&1 | cut -c1-300
docker inspect -f '{{range .State.Health.Log}}{{.Start}} exit={{.ExitCode}}{{"\n"}}{{end}}' "$C" 2>&1 | tail -6 | cut -c1-80
echo "=== 기동 사건"
docker logs "$C" 2>&1 | grep -oE '"event_type": "(api_started|trader_follower|trader_promoted|live_run_resumed|autostart[a-z_]*|restore[a-z_]*)"' | sort | uniq -c
echo "=== 첫 · 마지막 로그 시각"
docker logs "$C" 2>&1 | grep -oE '"ts": "[^"]+"' | head -1
docker logs "$C" 2>&1 | grep -oE '"ts": "[^"]+"' | tail -1
echo "=== 호스트 메모리 · 커널 OOM(최근)"
free -m | head -3
dmesg 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -5 | cut -c1-160
echo "=== 컨테이너 메모리(지금)"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>&1 | head -12
