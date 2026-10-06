#!/usr/bin/env bash
# 실계좌 한 바퀴 교차 점검 1 (읽기 전용 · 값만 · 2026-10-06) — 호스트 · 컨테이너 · 감시 경보 · 감사 · 오류 · 원장 · 판.
#   bash scripts/ops/remote.sh scripts/ops/probe_sweep_1006.sh
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
LOG=/tmp/sweep_api.log
docker logs --timestamps --since 26h "$API" > "$LOG" 2>&1
echo "=== $(date -u +%m-%d_%H:%MZ) 리더 $API · 기동 $(docker inspect -f '{{.State.StartedAt}}' "$API" | cut -c1-19)"
echo "=== 호스트"; uptime | sed -E 's/.*load/load/'; free -m | grep -E "Mem|Swap"; df -h / | tail -1
echo "=== 컨테이너"; docker ps -a --format "{{.Names}}\t{{.Status}}" | sort
echo "=== 로그 범위"; head -n 1 "$LOG" | cut -c1-19; tail -n 1 "$LOG" | cut -c1-19; wc -l < "$LOG"

echo "=== 감시 경보(run_watch_alarm · 26h) — 코드 · 종목별 수 · 마지막"
grep 'run_watch_alarm' "$LOG" | grep -oE '^[0-9T:-]{16}|"code": "[a-z_]+"|"detail": "[A-Z0-9_]+' | paste - - - | awk '{print $2, $3, $4, $5, $1}' | sort | awk '{k=$1" "$2" "$3" "$4; n[k]++; last[k]=$5} END {for (k in n) print n[k], k, "마지막", last[k]}' | sort -k2 | head -n 30

echo "=== 자가 감사(live_audit_found · 26h) — 코드 · 종목별 수"
grep 'live_audit_found' "$LOG" | grep -oE '"code": "[a-z_]+"|"symbol": "[A-Z0-9_]+"' | paste - - | sort | uniq -c | sort -rn | head -n 25

echo "=== ERROR · WARNING 종류(26h)"
grep -E '"level": "(ERROR|WARNING|error|warning)"' "$LOG" | grep -oE '"event_type": "[a-z_0-9]+"' | sort | uniq -c | sort -rn | head -n 40

echo "=== 6시간 ERROR · WARNING 종류(지금도 나나)"
SINCE6=$(date -u -d '6 hours ago' +%Y-%m-%dT%H:%M)
awk -v s="$SINCE6" '$1 >= s' "$LOG" | grep -E '"level": "(ERROR|WARNING|error|warning)"' | grep -oE '"event_type": "[a-z_0-9]+"' | sort | uniq -c | sort -rn | head -n 25

echo "=== Traceback 수(26h) · 처음 줄 종류"
grep -c 'Traceback' "$LOG"
grep -A1 -E '^[0-9T:.Z-]+ +[A-Za-z]*Error|Exception:' "$LOG" | grep -oE '[A-Za-z]+(Error|Exception)[^"]{0,90}' | sort | uniq -c | sort -rn | head -n 10

echo "=== 사건 종류 상위(4h)"
SINCE4=$(date -u -d '4 hours ago' +%Y-%m-%dT%H:%M)
awk -v s="$SINCE4" '$1 >= s' "$LOG" | grep -oE '"event_type": "[a-z_0-9]+"' | sort | uniq -c | sort -rn | head -n 45

echo "=== 전 판 정지 스위치"
$Q -c "select coalesce(max(value), '(없음)') from app_settings where key='live_entries_halted'"
echo "=== 열린 실계좌 판 — 수 · 같은 종목 둘 · 매매법 버전 · 연 시각(UTC)"
$Q -c "select count(*) from wf_runs where live and closed_at is null"
$Q -c "select symbol, count(*) from wf_runs where live and closed_at is null group by 1 having count(*) > 1"
$Q -c "select playbook, count(*), min(to_char(opened_at at time zone 'UTC','MM-DD HH24:MI')), max(to_char(opened_at at time zone 'UTC','MM-DD HH24:MI')) from wf_runs where live and closed_at is null group by 1"
echo "=== 열린 판의 열린 매매(종목|다리|방향|결과|진입 UTC|진입가|계약)"
$Q -c "select r.symbol, split_part(t.playbook,'@',1), t.direction, t.outcome, to_char(coalesce(t.opened_at,t.placed_at) at time zone 'UTC','MM-DD HH24:MI'), t.entry, t.contracts from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.closed_at is null and t.closed_at is null order by 5"
echo "=== 닫힌 판에 남은 열린 매매(찌꺼기)"
$Q -c "select r.symbol, split_part(t.playbook,'@',1), t.outcome, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), to_char(r.closed_at at time zone 'UTC','MM-DD HH24:MI') from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.closed_at is not null and t.closed_at is null order by 4"
echo "=== 26h 진입 · 청산(종목|다리|방향|진입 UTC|청산 UTC|진입가|청산가|결과)"
$Q -c "select r.symbol, split_part(t.playbook,'@',1), t.direction, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'),'열림'), t.entry, coalesce(t.exit_price::text,''), t.outcome from wf_trades t join wf_runs r on r.id=t.run_id where r.live and (t.opened_at >= now() - interval '26 hours' or t.closed_at >= now() - interval '26 hours') order by t.opened_at"
rm -f "$LOG"
