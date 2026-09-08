#!/usr/bin/env bash
# 2026-09-08 — 데모 API 가 실계좌 logs 볼륨에 남긴 것을 지운다. **api_demo 가 자기 볼륨(demologs)으로 다시 뜬 뒤**에 돌린다.
#   ① equity/daily.jsonl 의 테스트넷 줄(total 10078.…) — 백업 뒤 삭제
#   ② logs/walkforward 의 데모 판 파일 — 실계좌 DB 에 없는 열쇠만 (있으면 절대 안 지운다)
set -u
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== api = $API"
echo "--- api_demo 마운트 (runlogs 면 아직 분리 안 됨 → 중단)"
DEMO_MOUNT=$(docker inspect updown_live-api_demo-1 --format '{{range .Mounts}}{{.Name}} {{.Destination}}{{"\n"}}{{end}}' | grep " /app/logs$" | cut -d' ' -f1)
echo "api_demo /app/logs <- $DEMO_MOUNT"
case "$DEMO_MOUNT" in *demologs) ;; *) echo "⛔ api_demo 가 아직 runlogs 를 쓴다 — 배포 뒤에 돌린다"; exit 1;; esac

echo "--- ① equity/daily.jsonl"
docker exec "$API" sh -c 'cd /app/logs/equity && cp daily.jsonl daily.jsonl.bak-20260908 && grep -c "\"total\": \"10078" daily.jsonl; grep -v "\"total\": \"10078" daily.jsonl > daily.tmp && mv daily.tmp daily.jsonl && echo "남은 줄:" && wc -l < daily.jsonl && tail -2 daily.jsonl | cut -c1-120'

echo "--- ② walkforward 대기 파일 — 실계좌 DB 의 판 열쇠와 대조"
LIVE_KEYS=$(docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select key from wf_runs" | tr '\n' ' ')
for f in $(docker exec "$API" sh -c 'ls /app/logs/walkforward'); do
  key="${f%.json}"
  case " $LIVE_KEYS " in
    *" $key "*) echo "  keep   $f (실계좌 판)";;
    *) echo "  remove $f (실계좌 DB 에 없음 — 데모 판)"; docker exec "$API" rm -f "/app/logs/walkforward/$f";;
  esac
done
echo "--- 끝"
