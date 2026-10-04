#!/usr/bin/env bash
# T359 — 실계좌 숏 다리(삼각 숏 · MACD 숏)가 왜 0건인가 (읽기 전용 · 값만 · 2026-10-04).
#   로그(기동 뒤)의 진입 보류 사건을 이유 · 매매법 · 종목으로 센다 + 실계좌 판 매매법 구성.
#   bash scripts/ops/remote.sh scripts/ops/probe_short_legs_live.sh
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다 — 사건 이름 · 매매법 · 종목 · 이유만.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 컨테이너 ${API} 기동 시각(UTC)"
docker inspect -f '{{.State.StartedAt}}' "$API"
echo "=== 진입 보류 사건 수(기동 뒤 · 사건 이름)"
docker logs "$API" 2>&1 | grep -oE '"event": "session_entry_[a-z_]+"' | sort | uniq -c | sort -rn
echo "=== 진입 보류 사건 — 매매법별(기동 뒤 · 사건 x 매매법)"
docker logs "$API" 2>&1 | grep -E '"event": "session_entry_[a-z_]+held"' \
  | sed -E 's/.*"event": "([a-z_]+)".*"playbook": "([^"]+)".*/\1 \2/; t; s/.*"event": "([a-z_]+)".*/\1 (매매법 칸 없음)/' \
  | sort | uniq -c | sort -rn | head -30
echo "=== 보류 사건 한 줄 예시(삼각 · MACD 숏 · 이유 칸만 · 최대 6)"
docker logs "$API" 2>&1 | grep -E 'session_entry_[a-z_]+held' | grep -E 'private_strategy|private_strategy' \
  | grep -oE '"(event|playbook|reason|ref_return|band|surge|cap|rate|symbol|direction|note)": "?[^",}]*' | paste -d' ' - - - - - | head -6 | cut -c1-260
echo "=== 진입 · 후보 관련 다른 사건(기동 뒤 · 이름에 entry · candidate · propose)"
docker logs "$API" 2>&1 | grep -oE '"event": "[a-z_]*(entry|candidate|propos|opened|filled)[a-z_]*"' | sort | uniq -c | sort -rn | head -20
