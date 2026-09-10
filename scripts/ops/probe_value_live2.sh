#!/usr/bin/env bash
# 저평가 화면 — 브라우저 요청(2h) · 준비(warm) 이벤트 · EDGAR/아웃바운드 실패 상세 · 감사 발견 · DB 사실 수 (값 없음).
cd ~/updown 2>/dev/null || exit 1
A=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== nginx 2h: /api/fundamentals (경로 · 상태 · rt)"
docker logs --since 2h updown_live-web-1 2>&1 | grep "/api/fundamentals" | grep -oE '\[[^]]+\] "(GET|POST) /api/fundamentals/[a-z_]+[^"]*" [0-9]+ .*rt=[0-9.]+' | sed -E 's/\?[^"]*"/"/; s/ [0-9]+ "[^"]*" "[^"]*"//' | tail -8 | cut -c1-120
echo "=== $A 2h: screen_* / ranking_* 이벤트"
docker logs --since 2h "$A" 2>&1 | grep -oE '"ts": "[^"]+"|"event_type": "(screen_[a-z_]+|ranking_[a-z_]+|fundamentals_[a-z_]+)"' | paste - - 2>/dev/null | grep event_type | tail -10
docker logs --since 2h "$A" 2>&1 | grep -oE '"event_type": "(screen_[a-z_]+|ranking_[a-z_]+)"' | sort | uniq -c
echo "=== $A 2h: outbound_failed / edgar 상세"
docker logs --since 2h "$A" 2>&1 | grep -E 'outbound_failed|edgar_tickers_unavailable|screen_warm_failed' | grep -oE '"venue": "[^"]+"|"path": "[^"]{0,80}"|"status": [0-9]+|"detail": "[^"]{0,160}"|"error": "[^"]{0,160}"' | sort | uniq -c | sort -rn | head -8
echo "=== $A 2h: live_audit_found 상세"
docker logs --since 2h "$A" 2>&1 | grep live_audit_found | tail -1 | grep -oE '"payload": \{.{0,400}' | cut -c1-420
echo "=== DB: 사실·종목"
docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "select count(distinct symbol) as symbols, count(*) as rows from financial_facts" 2>&1 | head -2
docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "select market, count(*) from instruments where market in ('NASDAQ','NYSE') group by 1" 2>&1 | head -3
echo "=== $A: screen_price_missing 상세 (2h)"
docker logs --since 2h "$A" 2>&1 | grep screen_price_missing | grep -oE '"detail": "[^"]{0,120}"' | sort | uniq -c | sort -rn | head -4
