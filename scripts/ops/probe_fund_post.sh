#!/usr/bin/env bash
# 펀드 생성 POST 가 api 안에서 어떻게 끝났나 — uvicorn 접근 줄 · 그 분의 오류 (값 없음).
cd ~/updown 2>/dev/null || exit 1
A=updown_live-api_b-1
echo "== POST /rebalancer 접근 줄 (3h)"; docker logs --since 3h $A 2>&1 | grep -E 'POST /rebalancer' | tail -5 | cut -c1-120
echo "== 17:34 분 줄 수"; docker logs --since 3h $A 2>&1 | grep -c "17:34:"
echo "== 17:34:2x~3x 의 오류·경고·예외 (앞 200자)"
docker logs --since 3h $A 2>&1 | grep -E '17:34:(2|3|4)' | grep -iE 'error|warning|exception|traceback|fund|rebalancer|kill|shutdown|restart|worker' | cut -c1-220 | head -10
echo "== 17:33~17:35 event_type 종류"
docker logs --since 3h $A 2>&1 | grep -E '"ts": "2026-09-10T17:3[345]' | grep -oE '"event_type": "[^"]{0,60}"' | sort | uniq -c | sort -rn | head -12
echo "== 18:07 분 (두 번째 502 무렵)"
docker logs --since 3h $A 2>&1 | grep -E '"ts": "2026-09-10T18:0[78]' | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]{0,80}"' | sort | uniq -c | head -6
