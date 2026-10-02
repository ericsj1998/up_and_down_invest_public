#!/usr/bin/env bash
# 데모 API 상태 (읽기 전용) — 꺼져 있어도 남은 것: .demo_off 표식 · 데모 볼륨의 펀드 파일 · 데모 DB 의 열린 판 · 최근 CPU.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "== $(date -u +%H:%M:%SZ)"
echo "== 표식"; ls -la .demo_off 2>&1 | cut -c1-120
echo "== 데모 컨테이너"; docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'api_demo|migrate_demo'
echo "== 데모 볼륨 펀드 파일"
VOL=$(docker volume ls --format '{{.Name}}' | grep -E 'demologs$' | head -1)
echo "volume: $VOL"
docker run --rm -v "$VOL":/v alpine sh -c 'ls -la /v/funds 2>/dev/null | tail -n +2; ls /v 2>/dev/null' 2>&1 | head -20
echo "== 데모 DB 열린 판"
docker exec updown_live-postgres-1 psql -U updown -d updown_demo -tAc "select market, count(*), coalesce(round(sum(coalesce(margin_budget,0))::numeric,0),0) from wf_runs where closed_at is null group by market" 2>&1 | head -5
docker exec updown_live-postgres-1 psql -U updown -d updown_demo -tAc "select count(*) from wf_runs" 2>&1 | head -2
echo "== 호스트 CPU · 메모리(지금)"
top -bn1 | head -5 | tail -4
free -m | head -3
echo "== 컨테이너 CPU · 메모리"
docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | head -12
nproc
