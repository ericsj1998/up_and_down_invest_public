#!/usr/bin/env bash
# api 컨테이너 CPU 폭주 원인 (읽기 전용) — 10:30Z 이후 판 생성 · 펀드 전환 · 되살리기 · 예비 신호 · 자원 박동 · DB 풀 사건의 시각.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker logs --since 70m "$API" > /tmp/burn.log 2>&1
echo "== 분 단위 로그 줄 수(10:30Z ~)"
grep -oE '"ts": "[0-9-]+T1[01]:[0-9]{2}' /tmp/burn.log | sed 's/.*T//' | sort | uniq -c | awk '$1>0' | tail -n 50 | paste -sd' '
echo "== 판 · 펀드 사건 종류(10:30Z ~ · HTTP 제외)"
grep -v 'HTTP Request' /tmp/burn.log | grep -oE '"event_type": "(wf_run[a-z_]*|live_run[a-z_]*|live_runner[a-z_]*|fund[a-z_: ]{0,30}|live_session[a-z_]*|run_start[a-z_]*|live_autostart[a-z_]*|gate_ws[a-z_]*|leader[a-z_]*|trader[a-z_]*|autostart[a-z_]*|live_close[a-z_]*|hold_after[a-z_]*|preview_[a-z_]*|resource[a-z_]*)' | sort | uniq -c | sort -rn | head -n 20
echo "== 판 시작 · 전환 · 닫힘 첫 5 · 마지막 5 시각"
grep -E 'wf_run_opened|live_runner_started|fund_playbook|fund_switch|fund_update|wf_run_closed|live_close' /tmp/burn.log | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,40}|"symbol": "[^"]+"' | paste -sd' ' | sed 's/"ts": /\n/g' | sed -n '1,5p;$p'
echo "== resource_beat 원문(최근 4)"
grep -E 'resource_beat|resource_snapshot|"cpu"' /tmp/burn.log | grep -v 'Running job\|executed' | tail -n 4 | cut -c1-300
echo "== 스레드 · 프로세스"
docker top "$API" -eo pid,pcpu,etime,comm 2>/dev/null | sort -k2 -rn | head -n 6
rm -f /tmp/burn.log
