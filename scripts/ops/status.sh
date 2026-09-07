#!/usr/bin/env bash
# 서버 한눈에 — 호스트 · 컨테이너 · 어느 슬롯이 리더인가 · 최근 오류 · 브라우저 트래픽.
# 화면 문제는 **여기 "browser traffic" 부터 본다** (서버 프로브가 200 이어도 브라우저는 499 일 수 있다).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
# ⚠️ api_demo 를 빼야 한다 — 배포 직후엔 그 컨테이너가 먼저 나와 데모(테스트넷)의 오류를 실계좌 것으로 보여 준다
#    (2026-09-07 v1.1.0 배포 뒤 실측: NEAR 400 · fund_restore_failed · daily_report_not_scheduled 전부 데모였다).
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== host"; uptime | sed -E 's/.*load/load/'; free -m | grep -E "Mem|Swap"
vmstat 1 3 | tail -2 | awk '{print "cpu: user="$13"% idle="$15"% steal="$17"%   (steal>20 지속이면 버스트 크레딧 소진 → deploy.md §12-5)"}'
echo "=== containers"; docker ps -a --format "{{.Names}}\t{{.Status}}\t{{.Image}}" | sort
echo "=== env (값은 안 찍는다)"; grep -E "^(IMAGE_TAG|LIVE_ORDERS|UPDOWN_MARKETS)=" .env.live
echo "=== leader"; docker logs "$API" 2>&1 | grep -oE '"event_type": "(trader_promoted|trader_follower|live_run_resumed)"' | sort | uniq -c
echo "=== errors/warnings (30m)"
docker logs --since 30m "$API" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]+"|"error": "[^"]{0,120}' | sort | uniq -c | sort -rn | head -8
echo "=== browser traffic (5m · nginx) — path status count"
docker logs --since 5m updown_live-web-1 2>&1 | grep Mozilla | grep -oE '"GET /api/[^" ?]+[^"]*" [0-9]+' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -10
echo "=== nginx non-2xx (5m)"
docker logs --since 5m updown_live-web-1 2>&1 | grep Mozilla | grep -vE '" (200|204|304) ' | grep -oE '"[A-Z]+ /api/[^" ?]+[^"]*" [0-9]+' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -6
echo "=== open runs (DB)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select symbol, leverage, margin_budget, opened_at::time from wf_runs where closed_at is null order by symbol"
echo "=== disk"; df -h / | tail -1; docker system df | head -4
