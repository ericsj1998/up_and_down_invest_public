#!/usr/bin/env bash
# 1.45.0 배포 뒤 두 번째 — 펀드 문 붙음 · 엔진 다리 · 목표 지정가 다시 걸기 · 오류(읽기만)
set -u
A=updown_live-api-1
docker logs --since 20m "$A" 2>&1 | grep -oE '"event_type": "(fund|live_fund|engine|nas|target|live_target|live_pending|share|wf_engine)[^"]*"' | sort | uniq -c | sort -rn | head -20
echo "=== warning 수 · 앞 5"
docker logs --since 20m "$A" 2>&1 | grep -c '"level": "warning"'
docker logs --since 20m "$A" 2>&1 | grep '"level": "warning"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -8
echo "=== error 수"
docker logs --since 20m "$A" 2>&1 | grep -c '"level": "error"'
