#!/usr/bin/env bash
# 1.27.0 교체 뒤 3 (읽기 전용) — 옛 리더 10:00:27 ~ 10:00:40Z 원문 · 새 리더 감사 발견 · 펀드 부착 · 되살리기 수.
echo "== 옛 리더 api_b · 10:00:20 ~ 10:00:45Z 비-HTTP 원문(300자)"
docker logs updown_live-api_b-1 2>&1 | grep -E '"ts": "[0-9-]+T10:00:(2[0-9]|3[0-9]|4[0-5])' | grep -v 'HTTP Request' | grep -v live_feed_backfilled | cut -c1-300
echo "== 옛 리더 api_stopping · engine_lock_released 시각"
docker logs updown_live-api_b-1 2>&1 | grep -E 'api_stopping|engine_lock_released|inproc_engine_stopped' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"' | paste -sd' '
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs "$API" > /tmp/new.log 2>&1
echo "== 새 리더 $(date -u +%H:%M:%SZ) — 되살린 판 $(grep -c '"event_type": "live_run_resumed"' /tmp/new.log) · 펀드 대기 $(grep -c '"event_type": "live_awaiting_fund"' /tmp/new.log)"
echo "-- 감사 발견 원문"
grep live_audit_found /tmp/new.log | cut -c1-400 | tail -n 3
echo "-- 펀드 관련 사건(event_type 에 fund)"
grep -oE '"event_type": "[^"]*fund[^"]{0,60}' /tmp/new.log | sort | uniq -c | sort -rn | head -n 10
echo "-- ETH 판(live0cade27b) 새 리더 사건"
grep 'live0cade27b' /tmp/new.log | grep -v 'HTTP Request' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,60}|"note": "[^"]{0,60}"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 8
rm -f /tmp/new.log
