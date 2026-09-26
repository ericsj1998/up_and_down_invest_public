#!/usr/bin/env bash
# 오늘(UTC) 실계좌 앱 로그에서 진입 관련 사건만 센다 — 배포로 컨테이너 로그가 끊겨도 회전 파일은 남는다 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_today_entries.sh
#
# 왜(2026-09-26): "놓친 매매가 없나" — 공개 봉 재계산이 닿지 않는 오늘 몫(자정 뒤)을 서버 기록으로 메운다.
F="/var/lib/docker/volumes/updown_live_runlogs/_data/app/api-$(date -u +%Y-%m-%d).jsonl"
echo "=== $F · $(date -u +%FT%TZ)"
sudo -n test -f "$F" || { echo "파일 없음"; exit 0; }
echo "=== 진입 · 문 · 실패 사건 수"
sudo -n grep -oE '"event_type": "(fund_gate_breadth|session_entry_gate_held|session_entry_ref_gate_held|session_entry_gate_fit|live_runner_step_failed|live_runner_sizing|live_runner_unfillable|live_entry_unfilled|live_underfunded|gate_order_submit|live_filled_exposure|live_margin_shared)"' "$F" \
  | sort | uniq -c
echo "(아무것도 없으면 = 오늘 자리 · 문 · 실패가 없었다)"
echo "=== 걸음 실패 · 문 막힘 줄(최대 10)"
sudo -n grep -E '"event_type": "(live_runner_step_failed|session_entry_gate_held|session_entry_ref_gate_held)"' "$F" | cut -c1-300 | tail -10
