#!/usr/bin/env bash
# CPU 급등 원인 탐침 2 (읽기 전용) — api_b 08:46 ~ 08:50Z 비-HTTP 사건 · 경고 · 오류 · price_orders 호출 종목 · 최근 진입 · 손절 사건.
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs --since 60m "$API" > /tmp/api_spike.log 2>&1
echo "== 08:46 ~ 08:50Z 비-HTTP 사건 종류"
grep -E '"ts": "[^"]+T08:(46|47|48|49|50)' /tmp/api_spike.log | grep -v 'HTTP Request' | grep -oE '"event_type": "[^"]{0,80}' | sort | uniq -c | sort -rn | head -n 30
echo "== 08:30 ~ 09:05Z 경고 · 오류 (level warning/error) 종류"
grep -E '"ts": "[^"]+T(08:[3-5][0-9]|09:0[0-5])' /tmp/api_spike.log | grep -E '"level": "(warning|error)"' | grep -oE '"event_type": "[^"]{0,80}' | sort | uniq -c | sort -rn | head -n 20
echo "== 08:48 이후 price_orders 호출 종목 상위"
grep -E '"ts": "[^"]+T(08:4[8-9]|08:5[0-9]|09:0[0-5])' /tmp/api_spike.log | grep -oE 'price_orders\?status=open&contract=[A-Z_]+' | sort | uniq -c | sort -rn | head -n 12
echo "== 08:48 이후 price_orders 를 부른 사건 앞뒤 (첫 3건 문맥)"
grep -nE 'price_orders\?status=open' /tmp/api_spike.log | grep -E 'T08:4[89]' | head -n 3 | cut -d: -f1 | while read -r n; do sed -n "$((n-3)),$((n+1))p" /tmp/api_spike.log | grep -oE '"event_type": "[^"]{0,100}|"symbol": "[^"]+"|"ts": "[^"]+"' | tr '\n' ' '; echo; done
echo "== 08:00 이후 진입 · 체결 · 손절 · 몫 · 되살리기 사건"
grep -E '"ts": "[^"]+T(08|09):' /tmp/api_spike.log | grep -oE '"event_type": "(live_(entry|fill|order|stop|share|add|revive|resync|reconcile|leader|board|fund)[a-z_]*|session_(entry|exit|stop)[a-z_]*|stop_dup[a-z_]*|autostart[a-z_]*)' | sort | uniq -c | sort -rn | head -n 25
echo "== 08:47 ~ 08:49 손절 · 몫 · 대조 사건 원문(최대 12줄 · 값 요약)"
grep -E '"ts": "[^"]+T08:4[7-9]' /tmp/api_spike.log | grep -E 'stop|share|reconcile|resync|dup|orphan' | grep -v 'HTTP Request' | cut -c1-260 | head -n 12
rm -f /tmp/api_spike.log
