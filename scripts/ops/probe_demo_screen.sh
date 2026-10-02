#!/usr/bin/env bash
# 데모 화면 문제 진단 (읽기 전용) — 프록시 접근 로그(브라우저가 실제로 보낸 요청 · 상태) · 데모 api 경고 · 오류 · 테스트넷 키 유무(값은 안 찍음).
cd ~/updown 2>/dev/null || exit 1
echo "== $(date -u +%H:%M:%SZ)"
echo "== 프록시 접근 로그 30분 — 4xx · 5xx (방법 · 경로 · 상태 · 수)"
docker logs --since 30m updown_live-proxy-1 2>&1 | grep -oE '"(method|uri|status)":("[^"]*"|[0-9]+)' | paste - - - | grep -E '"status":(4|5)[0-9][0-9]' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -n 20
echo "== 프록시 접근 로그 30분 — exchange · testnet · state 경로 상태"
docker logs --since 30m updown_live-proxy-1 2>&1 | grep -oE '"(uri|status)":("[^"]*"|[0-9]+)' | paste - - | grep -iE 'exchange|testnet|state|venue' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -n 12
echo "== 데모 api 경고 · 오류 30분(사건 이름 · 수)"
docker logs --since 30m updown_live-api_demo-1 2>&1 | grep -E '"level": "(warning|error)"|\[warning|\[error' | grep -oE '"event_type": "[^"]+"|http_5xx[^}]{0,200}' | sort | uniq -c | sort -rn | head -n 12
echo "== 데모 api http_5xx 상세"
docker logs --since 30m updown_live-api_demo-1 2>&1 | grep -E 'http_5xx' | grep -oE '"path": "[^"]+"|"status": [0-9]+|"detail": "[^"]{0,160}' | paste - - - | sort | uniq -c | head -n 8
echo "== 데모 api 테스트넷 키 유무(이름만)"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' updown_live-api_demo-1 2>/dev/null | grep -oE '^(GATE_TESTNET_API_KEY|GATE_TESTNET_API_SECRET|BINANCE_TESTNET_API_KEY|BINANCE_TESTNET_API_SECRET|UPDOWN_MARKETS)=' | sed 's/=$/ 있음/'
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' updown_live-api_demo-1 2>/dev/null | grep -E '^UPDOWN_MARKETS='
echo "== 데모 api 기동 이후 거래소 호출 요약"
docker logs --since 30m updown_live-api_demo-1 2>&1 | grep -oE '"venue": "[A-Z]+", "method": "[A-Z]+", "path": "[^"]+", "status": [0-9]+' | sed -E 's#/[A-Z0-9_]+_USDT#/<종목>#' | sort | uniq -c | sort -rn | head -n 10
