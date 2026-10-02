#!/usr/bin/env bash
# 봉 마감 직후 실계좌 api 사건 (읽기 전용) — 최근 2시간 · 05:00Z 전후 사건 종류 · 핵심 6종 관련 줄 요약.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) $API"
echo "== 사건 종류(2시간)"
docker logs --since 2h "$API" 2>&1 | grep -oE '"event_type": "[a-z_0-9]+' | sort | uniq -c | sort -rn | head -n 40
echo "== 05:00~05:10Z 사건(핵심 6종 · 출력 줄 요약)"
docker logs --since 2h "$API" 2>&1 | grep -E '"ts": "2026-10-02T05:0' | grep -E 'BTC_USDT|SOL_USDT|XRP_USDT|DOGE_USDT|ETH_USDT|ADA_USDT' | grep -oE '"event_type": "[a-z_0-9]+"|"symbol": "[A-Z_]+"|"(reason|why|setup|playbook|rule_id)": "[^"]{0,50}"' | paste -s -d' ' | fold -w 240 | head -n 30
echo "== 진입 정지 관련(2시간)"
docker logs --since 2h "$API" 2>&1 | grep -iE 'halt|blocked|skip' | grep -oE '"ts": "[^"]+"|"event_type": "[a-z_0-9]+"|"symbol": "[A-Z_]+"' | paste - - - | sort | uniq -c | tail -n 20
