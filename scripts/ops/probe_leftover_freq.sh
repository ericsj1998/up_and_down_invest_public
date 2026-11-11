#!/usr/bin/env bash
# 판 시작 · 청산 때 거둔 잔재(live_leftovers_swept) 기록 — 고아 손절이 얼마나 자주 남나(읽기만 · 두 슬롯 로그)
set -u
for C in updown_live-api_b-1 updown_live-api-1; do
  echo "=== $C"
  docker logs "$C" 2>&1 | grep '"live_leftovers_swept"' | grep -oE '"symbol": "[^"]+", "why": "[^"]+", "found": [0-9]+, "swept": [0-9]+, "detail": \[[^]]*\]|"ts": "[^"]{19}' | paste - - | cut -c1-200 | tail -20
done
