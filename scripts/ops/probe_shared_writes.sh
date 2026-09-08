#!/usr/bin/env bash
# 공유 logs 볼륨에 누가 무엇을 쓰나 — 펀드 파일 전문 · walkforward/app 디렉토리 · 두 컨테이너의 쓰기 이벤트
set -u
echo "=== 펀드 파일 (실계좌 볼륨)"
docker exec updown_live-api_b-1 sh -c 'python -c "import json;d=json.load(open(\"/app/logs/funds/fundea4ca81e.json\"));[print(k, str(v)[:160]) for k,v in d.items() if k!=\"basket\"]"' 2>&1
echo "=== logs/walkforward · logs/app"
docker exec updown_live-api_b-1 sh -c 'ls -la /app/logs/walkforward | head -12; ls -la /app/logs/app | head -6' 2>&1
echo "=== 쓰기 이벤트 24h — api_demo"
docker logs --since 24h updown_live-api_demo-1 2>&1 | grep -oE '"event_type": "(fund[a-z_]*saved|funds_[a-z_]+|fund_[a-z_]+|equity_snapshot[a-z_]*|pending_[a-z_]+|report_[a-z_]+)"' | sort | uniq -c | sort -rn | head -12
echo "=== 쓰기 이벤트 24h — api_b (리더)"
docker logs --since 24h updown_live-api_b-1 2>&1 | grep -oE '"event_type": "(fund[a-z_]*saved|funds_[a-z_]+|fund_[a-z_]+|equity_snapshot[a-z_]*|pending_[a-z_]+|report_[a-z_]+)"' | sort | uniq -c | sort -rn | head -12
echo "=== api_demo 의 판(run) — 데모 DB"
docker exec updown_live-postgres-1 psql -U updown -d updown_demo -tAc "select symbol, key, closed_at is null as open from wf_runs order by created_at desc limit 8" 2>&1 | head -8
