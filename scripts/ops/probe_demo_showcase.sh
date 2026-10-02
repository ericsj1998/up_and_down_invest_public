#!/usr/bin/env bash
# 쇼케이스 데모 배포 뒤 확인 (읽기 전용) — 데모 컨테이너 상태 · 쇼케이스 켜짐 · 되살린 판 0 · CPU 천장 · 1분 CPU 표본(데모 · 실계좌 api).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "== $(date -u +%H:%M:%SZ) · 표식 $(ls .demo_off 2>/dev/null || echo 없음)"
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'api(_b|_demo)?-1'
echo "== 데모 설정(천장 · 몫 · 쇼케이스)"
docker inspect -f 'NanoCpus={{.HostConfig.NanoCpus}} CpuShares={{.HostConfig.CpuShares}} Memory={{.HostConfig.Memory}}' updown_live-api_demo-1 2>&1
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' updown_live-api_demo-1 2>/dev/null | grep -E '^UPDOWN_SHOWCASE=|^LIVE_ORDERS='
echo "== 데모 로그(쇼케이스 · 되살린 판이 없어야 한다)"
docker logs --since 15m updown_live-api_demo-1 2>&1 | grep -oE 'showcase_mode|event_type": "(live_runner_started|wf_run_resumed|trader_promoted|funds_restored[^"]*)' | sort | uniq -c
echo "== 1분 CPU 표본(10초 x 6)"
for i in 1 2 3 4 5 6; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | grep -E 'api(_b|_demo)?-1'; sleep 10; done
echo "== 호스트"; top -bn1 | sed -n 3p; free -m | sed -n 2,3p
