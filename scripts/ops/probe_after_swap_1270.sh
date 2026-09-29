#!/usr/bin/env bash
# 1.27.0 교체 뒤 (읽기 전용) — 새 리더의 되살리기 진행 · 펀드 문 부착 · 첫 판정 · 10:00Z 봉 후보 · 손절.
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
echo "== 리더 $API · $(date -u +%H:%M:%SZ)"
docker logs "$API" 2>&1 > /tmp/new.log
echo "되살린 판 $(grep -c '"event_type": "live_run_resumed"' /tmp/new.log) · 러너 시작 $(grep -c '"event_type": "live_runner_started"' /tmp/new.log) · 펀드 대기 $(grep -c '"event_type": "live_awaiting_fund"' /tmp/new.log)"
echo "-- 펀드 · 문 · 준비 사건"
grep -oE '"event_type": "(fund[a-z_]*|live_fund[a-z_]*|gates?_[a-z_]*|live_fund_ready|fund_ready[a-z_]*)"' /tmp/new.log | sort | uniq -c | sort -rn | head -n 12
echo "-- 판정 · 걸음 · 진입 · 예비 신호 사건"
grep -oE '"event_type": "(live_step[a-z_]*|session_[a-z_]*|live_entry[a-z_]*|live_order[a-z_]*|preview_found|live_judg[a-z_]*|live_bar[a-z_]*)"' /tmp/new.log | sort | uniq -c | sort -rn | head -n 15
echo "-- 마지막 10줄 사건(HTTP 제외)"
grep -v 'HTTP Request' /tmp/new.log | tail -n 10 | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,70}|"symbol": "[^"]+"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 10
echo "== 옛 리더(api_b) 마지막 예비 신호 · 후보 (09:30 ~ 10:01Z)"
docker logs updown_live-api_b-1 2>&1 | grep -E 'T09:(3|4|5)[0-9]|T10:0[01]' | grep -E 'preview_found|cand:|entered:|session_entry|live_entry' | grep -v 'HTTP Request' | cut -c1-260 | tail -n 12
echo "옛 리더 09:30 ~ 10:01Z preview_found 수: $(docker logs updown_live-api_b-1 2>&1 | grep -E 'T09:(3|4|5)[0-9]|T10:0[01]' | grep -c preview_found)"
echo "== 거래소 손절(지금)"
cd ~/updown && docker exec "$API" python - <<'EOF' 2>/dev/null | tail -n 6
import asyncio, os
from updown.marketdata.gate.trade_client import GateTradeClient
from updown.marketdata.gate.client import LIVE_BASE_URL
async def main():
    c = GateTradeClient(os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"], base_url=LIVE_BASE_URL)
    stops = await c.list_stops(None)
    print("STOP_ORDERS", len(stops), [ (s.get("initial",{}).get("contract"), s.get("initial",{}).get("size"), s.get("trigger",{}).get("price")) for s in stops])
    pos = [p for p in await c.get_positions() if str(p.get("size")) not in ("0","")]
    print("POSITIONS", [(p.get("contract"), p.get("size")) for p in pos])
    await c.aclose()
asyncio.run(main())
EOF
rm -f /tmp/new.log
