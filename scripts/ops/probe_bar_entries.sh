#!/usr/bin/env bash
# 봉 마감 직후 진입이 제대로 됐나 (읽기 전용 · 2026-09-30) — 두 API 슬롯의 로그에서 마감 전 예비 신호 · 후보 · 진입 · 주문 · 리더 사건.
# 사용: probe_bar_entries.sh 09:55 10:12   (UTC 창 · HH:MM)
A="${1:-09:55}"; B="${2:-10:12}"
for C in updown_live-api-1 updown_live-api_b-1; do
  docker ps -a --format '{{.Names}} {{.Status}}' | grep -q "^$C " || { echo "== $C 없음"; continue; }
  echo "== $C ($(docker ps -a --format '{{.Names}} {{.Status}}' | grep "^$C " | cut -d' ' -f2-))"
  docker logs --since 3h "$C" 2>&1 | awk -v f="$A" -v t="$B" '{ if (match($0, /"ts": "[0-9-]+T[0-9][0-9]:[0-9][0-9]/)) { h=substr($0, RSTART+18, 5); if (h>=f && h<=t) print } }' > /tmp/bar.log
  echo "-- 줄 $(wc -l < /tmp/bar.log) · 사건 종류(HTTP 제외)"
  grep -v 'HTTP Request' /tmp/bar.log | grep -oE '"event_type": "[^"]{0,60}' | sort | uniq -c | sort -rn | head -n 30
  echo "-- 예비 신호 · 후보 · 진입 · 주문 · 리더 · 보류 원문(값 요약)"
  grep -E 'preview_found|session_entry|live_entry|live_order|order_submitted|trader_promoted|trader_follower|live_run_resumed|entry_hold|session_blocked|awaiting|fund_gate|leader|cand:|entered:' /tmp/bar.log | grep -v 'HTTP Request' | cut -c1-330 | head -n 30
done
rm -f /tmp/bar.log
