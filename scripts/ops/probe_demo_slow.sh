#!/usr/bin/env bash
# 데모 화면 느림 진단 (읽기 전용) — web(nginx) 로그의 499 · 느린 요청 원문 꼬리 · 데모 api 로그의 exchange/balances · testnet 관련 줄.
cd ~/updown 2>/dev/null || exit 1
echo "== $(date -u +%H:%M:%SZ) web 로그 형식 견본(마지막 3줄)"
docker logs --since 10m updown_live-web-1 2>&1 | tail -n 3 | cut -c1-300
echo "== web 로그 30분 499 · 5xx 줄(원문 앞 300자)"
docker logs --since 30m updown_live-web-1 2>&1 | grep -E '" (499|50[0-9]) ' | tail -n 12 | cut -c1-300
echo "== 데모 api 로그 30분 — balances · state · testnet · timeout · slow"
docker logs --since 30m updown_live-api_demo-1 2>&1 | grep -iE 'balances|exchange_state|testnet|timeout|timed out|slow|unavailable' | grep -oE '"event_type": "[^"]+"|"(path|detail|error|venue)": "[^"]{0,140}' | paste - - | sort | uniq -c | sort -rn | head -n 15
echo "== 실계좌 api outbound_failed 대상"
docker logs --since 40m updown_live-api-1 2>&1 | grep -E 'outbound_failed|outbound_retry' | grep -oE '"(venue|host|path|status|error)": [^,}]{0,80}' | sort | uniq -c | sort -rn | head -n 8
