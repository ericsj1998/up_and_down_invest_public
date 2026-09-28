#!/usr/bin/env bash
# 전환 뒤 지금 — 리더인가 · 러너가 붙은 판 수 · 마지막 fund/switch 이벤트 · Gate 403 본문 (읽기 전용 · 2026-09-29).
echo "== leader lock"; docker exec updown_live-redis-1 redis-cli get updown:api:trader 2>&1 | cut -c1-40; docker exec updown_live-redis-1 redis-cli ttl updown:api:trader
echo "== 최근 리더 이벤트"; docker logs --tail 20000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "(leader_|engine_lock|trader_lock|api_leader|became_leader|lock_acquired|leadership)' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"' | paste - - | tail -8
echo "== 전환 이벤트 (fund_rules_switched · fund_playbook · switch_failed · fund_saved)"; docker logs --tail 20000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "(fund_rules_switched|fund_playbook[a-z_]*|fund_switch[a-z_]*|fund_members_widened|fund_saved|fund_members_released|fund_gate_attached)"' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"playbook": "[^"]+"' | paste - - - | tail -10
echo "== _drop_one / live_run_dropped / run_start 시각별"; docker logs --tail 20000 updown_live-api_b-1 2>&1 | grep -E '"event_type": "(live_run_dropped|live_runner_dropped|run_start_requests|live_session_started|live_runner_stopped)"' | grep -E '"ts": "2026-09-28T17:(2|3)' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"symbol": "[^"]+"' | paste - - - | tail -14
echo "== Gate 403 본문"; docker logs --tail 20000 updown_live-api_b-1 2>&1 | grep -oE 'Gate 오류 응답\([^)]*\): 403 [^"]{0,160}' | sort | uniq -c | sort -rn | head -3
echo "== steal"; top -bn1 | sed -n 3p
