#!/usr/bin/env bash
# 펀드 판 띄운 시간 · 자원 · 락(읽기만)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 25m > /tmp/ev.txt 2>&1
S=$(grep '"event_type": "live_session_started"' /tmp/ev.txt | grep -oE '"ts": "[^"]+' | cut -c8-27)
echo "판 시작 수 $(echo "$S" | grep -c .) · 처음 $(echo "$S" | head -1) · 끝 $(echo "$S" | tail -1)"
grep -E 'fund_created|fund_gate_attached|rebalancer_created|fund_ready|trader_lock_lost|engine_lock_lost|gate_ws_reconnect|fund_restore_timing' /tmp/ev.txt | grep -oE '"ts": "[^"]+", ("trace_id": [^,]+, )?"event_type": "[a-z_]+"' | sed -E 's/"trace_id": [^,]+, //' | tail -8 | cut -c1-90
for f in candlesticks 'positions/' 'contracts/' order_book 'leverage' 'tickers/' accounts; do echo "$f $(grep -c "HTTP Request.*$f" /tmp/ev.txt)"; done | tr '\n' ' '; echo
grep -E '"level": "(error|critical)"' /tmp/ev.txt | tail -5 | cut -c1-220
free -m | awk '/Mem:/{print "가용 "$7} /Swap:/{print "스왑 "$3}'
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | grep -E 'api' | cut -c1-70
rm -f /tmp/ev.txt
