#!/usr/bin/env bash
# 진입 · 보류 사건 세부 (읽기 전용 · 18시간) — 주문 · 체결 · 손절 · 펀드 문 보류 · 거래량 기준 보류 · 스트림 끊김. 키 · 주소는 안 찍는다.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
for ev in gate_order_submit gate_paper_order_result live_filled_exposure gate_stop_placed session_entry_gate_held session_entry_gate_fit session_entry_ref_volpct_held live_stream_closed; do
  echo "== $ev"
  docker logs --since 18h "$API" 2>&1 | grep "\"$ev\"" | sed -E 's/"(api_key|secret|sign|signature|key)": "[^"]*"/"\1": "-"/g' | grep -oE '"ts": "[^"]{16}|"symbol": "[A-Z_]+"|"(side|size|price|status|reason|why|gate|playbook|run_key|exposure|fit|scale|cap|volpct|ref|threshold|contract|text|mode|testnet|live|label)": "?[^",}]{0,50}' | paste -sd' ' | fold -w 250 | head -n 8
done
echo "== 거래소 실주문 여부(환경 · 값 아님)"
docker exec "$API" sh -c 'printf "LIVE_ORDERS=%s\n" "${LIVE_ORDERS:-unset}"'
