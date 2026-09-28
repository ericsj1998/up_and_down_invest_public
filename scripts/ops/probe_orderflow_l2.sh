#!/usr/bin/env bash
# 층별 호가(1.25.2 · GATE_L2)가 서버 수집기에 쌓이는지 — 파일 수 · 줄 수 · 첫 줄 앞부분 · 체결가 범위 칸 (공개 시세 · 시크릿 없음).
#
#     bash scripts/ops/remote.sh scripts/ops/probe_orderflow_l2.sh
set -u
N=updown_live-orderflow-1
docker ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}" | grep orderflow
docker exec "$N" cat logs/orderflow/heartbeat.json; echo
docker exec "$N" sh -c 'ls logs/orderflow/GATE_L2 2>/dev/null | wc -l; f=$(ls logs/orderflow/GATE_L2/BTC_USDT/*.jsonl 2>/dev/null | tail -1); echo "$f"; wc -l < "$f"; head -c 160 "$f"; echo; g=$(ls logs/orderflow/GATE/XMR_USDT/*.jsonl | tail -1); grep trades "$g" | tail -1'
docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}" "$N"
free -m | grep -E "Mem|Swap"
