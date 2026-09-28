#!/usr/bin/env bash
# 펀드 전환(PUT /rebalancer/{id}/playbook) 이 서버 로그에 남긴 것 — 17:20 ~ 17:30Z (읽기 전용 · 2026-09-29).
docker logs --tail 20000 updown_live-api_b-1 2>&1 | grep -E '"ts": "2026-09-28T17:(2[0-9]|3[0-5])' | grep -E 'fund|switch|playbook|rebalancer|run_start|live_session_started|Traceback|"error"' | grep -vE 'HTTP Request|live_feed_backfilled|toss_|stored_candles|outbound_|gate_ws' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,80}|"symbol": "[^"]+"|"playbook": "[^"]{0,60}|"error": "[^"]{0,120}|"level": "[a-z]+"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -60
