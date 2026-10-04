#!/usr/bin/env bash
# 실계좌 서버 시계 대 거래소 시계(읽기 전용 · 2026-10-04 · 돌파 롱이 1H 마감 전에 체결된 원인 확인)
#   Gate 웹소켓 봉 "마감" 판정이 서버 시계(지금 ≥ 봉 시작 + 간격)로 되므로, 서버 시계가 빠르면 형성 중 봉으로 진입한다.
#   bash scripts/ops/remote.sh scripts/ops/probe_clock_skew.sh
#   🔴 env 를 읽지 않는다 · 비밀값 없음(Gate 공개 시각 끝점만).
for n in 1 2 3; do
  local_ms=$(date -u +%s%3N)
  gate_ms=$(curl -s --max-time 5 https://api.gateio.ws/api/v4/spot/time | grep -oE '[0-9]{13}')
  after_ms=$(date -u +%s%3N)
  if [ -n "$gate_ms" ]; then
    mid=$(( (local_ms + after_ms) / 2 ))
    echo "서버 - Gate = $(( mid - gate_ms )) ms (왕복 $(( after_ms - local_ms )) ms)"
  else
    echo "Gate 시각 못 받음"
  fi
  sleep 1
done
echo "=== timedatectl"
timedatectl 2>/dev/null | grep -E 'Local time|Universal|synchronized|NTP service' || echo "(timedatectl 없음)"
echo "=== 컨테이너 안 시각(api) 대 호스트"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "host $(date -u +%H:%M:%S) · container $(docker exec "$API" date -u +%H:%M:%S 2>/dev/null)"
