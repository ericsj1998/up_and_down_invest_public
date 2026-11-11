#!/usr/bin/env bash
# 1.45.0 배포 뒤 네 번째 — 펀드 복원 · 앵커 · 리밸런서 사건과 마지막 awaiting_fund 시각(읽기만)
set -u
A=updown_live-api-1
L=$(docker logs --since 40m "$A" 2>&1)
echo "$L" | grep -oE '"event_type": "[^"]*(rebal|anchor|restor|attach|member|gate_attached|playbook)[^"]*"' | sort | uniq -c | sort -rn | head -15
echo "=== awaiting_fund 첫 · 마지막 시각"
echo "$L" | grep '"event_type": "live_awaiting_fund"' | grep -oE '"(timestamp|ts|at)": "[^"]+"' | sed -n '1p;$p'
date -u '+지금 UTC %H:%M:%S'
