#!/usr/bin/env bash
# 라이브 룰 0.3 이 **놓친 진입 자리**가 있나 + 걸음이 도는가.
#
# 원장에 진입이 없을 때 답은 둘 중 하나다:
#   ① 신호가 애초에 없었다       → 정상 (룰 0.3 은 드물게 걸린다)
#   ② 신호는 있었는데 안 들어갔다 → 🔴 문·크기·체결 중 하나가 막은 것
# 로그만 보면 구별이 안 된다(안 걸린 것은 아무 줄도 안 남긴다). 그래서 **세션이 스스로 내는
# 깔때기(funnel)** 를 읽는다 — 자리까지 온 것과 문에서 걸린 것을 세션이 직접 센다.
set -uo pipefail
PG=updown_live-postgres-1
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
H=${1:-48}
echo "=== api=$API · $(date -u +%FT%TZ) ==="

echo
echo "=== ① 룰 0.3 판과 run_id ==="
docker exec "$PG" psql -U updown -d updown -tAc "
  select key || ' | ' || symbol || ' | ' || playbook_id || ' | lev ' || leverage
         || ' | 예산 ' || round(margin_budget,2) || ' | 연 ' || to_char(opened_at,'MM-DD HH24:MI')
    from wf_runs where closed_at is null order by symbol;"

echo
echo "=== ② 각 판의 세션 상태 (깔때기 · 마지막 걸음) ==="
for RID in $(docker exec "$PG" psql -U updown -d updown -tAc \
    "select key from wf_runs where closed_at is null order by symbol;"); do
  echo "--- $RID"
  # 🔴 `-i` 가 없으면 stdin 이 안 넘어가 python 이 **빈 프로그램으로 조용히 성공**한다 (규칙 #8).
  docker exec -i "$API" python - "$RID" <<'PY' || echo "  (읽기 실패)"
import json, sys, urllib.request
rid = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:8000/api/walkforward/live/{rid}", timeout=10) as r:
        d = json.load(r)
except Exception as exc:
    print(f"  API 못 읽음: {exc}"); raise SystemExit(0)
keys = ("symbol","steps","bars","last_step_at","last_bar_at","open_count","funnel",
        "gate_held","observe_only","last_error","failures","waiting","trades_count")
shown = False
for k in keys:
    if k in d:
        print(f"  {k} = {d[k]}"); shown = True
if not shown:  # 모양이 바뀌었을 수 있다 — 무엇이 오는지 보여준다 (조용한 실패 금지)
    print(f"  (기대한 열쇠가 없다) 열쇠들: {sorted(d)[:25]}")
PY
done

echo
echo "=== ③ 걸음 통계 (live_step_slow 의 payload) ==="
docker logs --since "${H}h" "$API" 2>&1 | grep '"live_step_slow"' | tail -3 | cut -c1-300

echo
echo "=== ④ 최근 ${H}시간 사건 상위 ==="
docker logs --since "${H}h" "$API" 2>&1 | grep -oE '"event_type": "[a-z_.]+"' | sort | uniq -c | sort -rn | head -15

echo
echo "=== ⑤ 막힘·오류 흔적 ==="
docker logs --since "${H}h" "$API" 2>&1 \
  | grep -oE '"event_type": "(session_entry_gate_held|live_runner_unfillable|live_filled_exposure|live_runner_sizing|live_entry_unfilled|live_margin_shared)"' \
  | sort | uniq -c
echo "(아무것도 없으면 = 문이 막은 적도, 진입을 시도한 적도 없다)"
