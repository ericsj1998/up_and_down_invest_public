#!/usr/bin/env bash
# 걸음 실패("미래를 요구했다")로 건너뛴 봉 — 언제 · 어느 종목. 폭(T289)을 형제 세션에 묻다 터진 자리다.
cd ~/updown 2>/dev/null || exit 1
for API in $(docker ps -a --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1"); do
  echo "=== $API (최근 72h)"
  docker logs --since 72h "$API" 2>&1 | grep '"event_type": "live_runner_step_failed"' \
    | grep -oE '"error": "[^"]{0,90}|"symbol": "[A-Z_]+"|"ts": "[0-9T:.-]+' | paste - - - 2>/dev/null | tail -n 30
done
echo "=== 열린 판의 매매 수 · 최근 닫힌 판의 매매 수 (A+ 펀드 포함)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, r.playbook, r.opened_at::timestamp(0), r.closed_at::timestamp(0), count(t.id) from wf_runs r left join wf_trades t on t.run_id=r.id where r.opened_at > now() - interval '3 days' group by 1,2,3,4 order by r.opened_at desc limit 30"
