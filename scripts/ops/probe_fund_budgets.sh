#!/usr/bin/env bash
# 펀드 생성 400(예산 합 > 계좌 총액) 진단 (읽기 전용) — 펀드 파일 · 열린 실계좌 판의 예산 합 · 판이 어느 펀드 몫인가 · 최근 펀드 사건.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) $API"
echo "== 펀드 파일"
docker exec "$API" sh -c 'ls -la --time-style=+%m-%dT%H:%M ${FUNDS_ROOT:-logs/funds}/ 2>/dev/null' | tail -n +2
echo "== 열린 판(DB · live) — 거래소 · 매매법 묶음 · 수 · 예산 합"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select market, count(*), round(sum(coalesce(margin_budget,0))::numeric, 2), count(distinct meta_json->>'fund'), string_agg(distinct coalesce(meta_json->>'fund', meta_json->>'fund_id', '-'), ',') from wf_runs where closed_at is null and live group by market" 2>&1 | head -5
echo "== 열린 판 열 이름(예산 칸 확인)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select column_name from information_schema.columns where table_name='wf_runs' order by ordinal_position" 2>&1 | tr '\n' ' ' | cut -c1-600; echo
echo "== 최근 60분 펀드 · 판 사건"
docker logs --since 60m "$API" 2>&1 | grep -oE '"event_type": "[a-z_]*(fund|rebalanc|live_run|budget)[a-z_]*' | sort | uniq -c | sort -rn | head -n 12
echo "== 최근 60분 400 응답(nginx · 경로)"
docker logs --since 90m updown_live-proxy-1 2>&1 | grep -E "rebalancer|funds" | grep -oE "\"(method|uri|status)\":(\"[^\"]*\"|[0-9]+)" | paste - - - | sort | uniq -c | sort -rn | head -n 8
