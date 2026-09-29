#!/usr/bin/env bash
# 새 리더에서 펀드 문 부착 뒤 판들이 풀렸나 (읽기 전용) — awaiting 발견의 마지막 시각 · 펀드 부착 시각 · 판별 steps.
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs "$API" > /tmp/new.log 2>&1
echo "== 펀드 부착 · 복원"
grep -E 'fund_gate_attached|funds_restored|fund_members_released|fund_ready' /tmp/new.log | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,60}|"note": "[^"]{0,80}"|"members": [0-9]+|"count": [0-9]+' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 6
echo "== awaiting_fund 감사 발견 — 첫 · 마지막 시각 · 수"
grep 'live_audit_found' /tmp/new.log | grep awaiting_fund | grep -oE '"ts": "[^"]+"' | sed -n '1p;$p'
echo "수 $(grep 'live_audit_found' /tmp/new.log | grep -c awaiting_fund)"
echo "== 지금 시각 $(date -u +%H:%M:%SZ) · 최근 5분 경고 · 오류"
docker logs --since 5m "$API" 2>&1 | grep -E '"level": "(warning|error)"' | grep -oE '"event_type": "[^"]{0,60}|"code": "[^"]+"' | sort | uniq -c | sort -rn | head -n 8
echo "== 판 40 러너 살아 있나 (live_runner_started 수 · 최근 gate_ws 재연결)"
echo "runner_started $(grep -c live_runner_started /tmp/new.log) · ws_subscribed $(grep -c gate_ws_subscribed /tmp/new.log)"
rm -f /tmp/new.log
