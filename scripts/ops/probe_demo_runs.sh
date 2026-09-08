#!/usr/bin/env bash
# 데모 API 가 공유 볼륨의 실계좌 펀드 파일로 무엇을 했나 — 데모 DB 판 · 데모 로그의 펀드/스냅샷 이벤트
set -u
echo "=== 데모 DB 판 (열린 것)"
docker exec updown_live-postgres-1 psql -U updown -d updown_demo -tAc "select symbol, key, live, to_char(updated_at,'MM-DD HH24:MI') from wf_runs where closed_at is null order by updated_at desc limit 10" 2>&1 | head -10
echo "=== 실계좌 DB 판 (열린 것)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select symbol, key, live, to_char(updated_at,'MM-DD HH24:MI') from wf_runs where closed_at is null order by updated_at desc limit 10" 2>&1 | head -10
echo "=== api_demo 로그 48h — fund/equity/rank/rebalanc 이벤트"
docker logs --since 48h updown_live-api_demo-1 2>&1 | grep -oE '"event_type": "[^"]*(fund|equity|rank|rebalanc|snapshot)[^"]*"' | sed 's/: fundea4ca81e.*//' | sort | uniq -c | sort -rn | head -14
echo "=== api_demo 로그 — equity_snapshot 원문 (48h)"
docker logs --since 48h updown_live-api_demo-1 2>&1 | grep "equity_snapshot" | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"total": "[^"]+"' | paste - - - | head -4
