#!/usr/bin/env bash
# 1.27.0 교체 뒤 2 (읽기 전용) — 옛 리더의 10:00:00 ~ 10:00:40Z 사건 · 예비 신호 3판의 정체 · 새 리더 펀드 부착 · 걸음 · 진입.
echo "== 옛 리더 api_b · 09:59 ~ 10:01Z 비-HTTP 사건 원문(값 요약)"
docker logs updown_live-api_b-1 2>&1 | grep -E '"ts": "[0-9-]+T(09:59|10:00|10:01)' | grep -v 'HTTP Request' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,80}|"symbol": "[^"]+"|"session_id": "[^"]+"|"note": "[^"]{0,80}"' | paste -sd' ' | sed 's/"ts": /\n/g' | head -n 30
echo "== 옛 리더 마지막 preview_found 원문"
docker logs updown_live-api_b-1 2>&1 | grep preview_found | tail -n 1 | cut -c1-400
echo "== 예비 신호 3판의 종목"
docker logs updown_live-api_b-1 2>&1 | grep -E '"session_id": "(live0cade27b|live5c3a6898|livee9752b16)"' | grep -oE '"session_id": "[^"]+"|"symbol": "[^"]+"' | paste -sd' ' | tr ' ' '\n' | sort -u | head -n 8
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs "$API" > /tmp/new.log 2>&1
echo "== 새 리더 $API · $(date -u +%H:%M:%SZ) — 되살린 판 $(grep -c '"event_type": "live_run_resumed"' /tmp/new.log) · 펀드 대기 $(grep -c '"event_type": "live_awaiting_fund"' /tmp/new.log)"
echo "-- 펀드 · 문 · 준비 · 걸음 · 진입 · 예비 사건 종류"
grep -v 'HTTP Request' /tmp/new.log | grep -oE '"event_type": "(fund[a-z_: ]{0,40}|live_fund[a-z_]*|[a-z_]*gate[a-z_]*|[a-z_]*ready[a-z_]*|live_step[a-z_]*|session_[a-z_]*|live_entry[a-z_]*|live_order[a-z_]*|preview_found|live_stop[a-z_]*|live_reconcil[a-z_]*)' | sort | uniq -c | sort -rn | head -n 20
echo "-- 새 리더 10:04Z 이후 비-HTTP 사건 종류 상위"
grep -E '"ts": "[0-9-]+T1[0-9]:(0[4-9]|[1-5][0-9])' /tmp/new.log | grep -v 'HTTP Request' | grep -oE '"event_type": "[^"]{0,60}' | sort | uniq -c | sort -rn | head -n 15
echo "-- 경고 · 오류"
grep -E '"level": "(warning|error)"' /tmp/new.log | grep -oE '"event_type": "[^"]{0,70}' | sort | uniq -c | sort -rn | head -n 10
rm -f /tmp/new.log
