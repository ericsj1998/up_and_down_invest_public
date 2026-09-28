#!/usr/bin/env bash
# 펀드 전환 뒤 "오류 · 재부팅" 신고 — 컨테이너 기동 시각 · 전환 요청 상태 · API 오류 · 자원 (읽기 전용 · 2026-09-29).
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_switch.sh
echo "== containers"; docker ps -a --format '{{.Names}}\t{{.Status}}' | head -12
echo "== api_b started / restarts"; docker inspect --format '{{.State.StartedAt}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' updown_live-api_b-1
echo "== mem"; free -m | sed -n 2p
echo "== steal"; top -bn1 | sed -n 3p
echo "== nginx: 펀드 · 리밸런서 요청 (마지막 15)"; docker logs --tail 3000 updown_live-web-1 2>&1 | grep -E '"(POST|PUT|PATCH|DELETE) /api/(rebalancer|fund|walkforward)' | tail -15 | cut -c1-230
echo "== nginx 5xx (마지막 10)"; docker logs --tail 3000 updown_live-web-1 2>&1 | grep -E '" 5[0-9]{2} ' | tail -10 | cut -c1-230
echo "== api_b error/traceback (마지막 12)"; docker logs --tail 3000 updown_live-api_b-1 2>&1 | grep -E '"level": "error"|Traceback|Error:' | tail -12 | cut -c1-300
echo "== api_b fund events (마지막 12)"; docker logs --tail 3000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "(fund_|api_started|leader|live_run_resumed|fund_gate)' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"' | paste - - | tail -12
echo "== dmesg oom"; dmesg 2>/dev/null | grep -i -E "oom|killed process" | tail -3
