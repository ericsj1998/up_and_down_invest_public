#!/usr/bin/env bash
# 1.45.0 배포 뒤 세 번째 — 오류 한 줄 본문(앞 400 자) · 펀드 문 붙음 관련 사건 이름(읽기만)
set -u
A=updown_live-api-1
docker logs --since 25m "$A" 2>&1 | grep '"level": "error"' | cut -c1-400
echo "=== fund 들어간 사건 이름"
docker logs --since 25m "$A" 2>&1 | grep -oE '"event_type": "[^"]*fund[^"]*"' | sort | uniq -c | sort -rn | head -12
