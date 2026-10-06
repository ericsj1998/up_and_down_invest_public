#!/usr/bin/env bash
# 1.35.1 배포 뒤 확인(읽기 전용 · 값만 · 주소 · 비밀값 없음) — 리더 · 펀드 되살림 · ETC 다시 구독 · DYDX 이어받기 · 수수료 맞춤 · 경고.
#   bash scripts/ops/remote.sh scripts/ops/probe_1351_after_deploy.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== $(date -u +%H:%M:%SZ) 리더 후보 $API · 기동 $(docker inspect -f '{{.State.StartedAt}}' "$API" | cut -c1-19)"
docker exec "$API" python -c "import urllib.request,json;h=json.load(urllib.request.urlopen('http://localhost:8000/health',timeout=10));print('trading_leader=',h.get('trading_leader'),'· version=',h.get('version'))" 2>&1 | tail -1
echo "== 컨테이너"; docker ps -a --format "{{.Names}}\t{{.Status}}" | grep -E 'api|web' | sort
LOG=/tmp/after1351.log
docker logs --timestamps --since 30m "$API" > "$LOG" 2>&1
echo "== 판 되살림 · 펀드(수)"
grep -oE '"event_type": "(live_run_resumed|funds_restored|fund_restore_failed|fund_gate_attached|fund_legs_refreshed|fund_brake_reset|trader_promoted|trader_follower)"' "$LOG" | sort | uniq -c
echo "== 펀드 파일 — legs_revision · 본 다리 낙폭 · 다리 노출"
docker exec -i "$API" python - <<'PY'
import glob, json
for p in sorted(glob.glob("/app/logs/funds/*.json")) or sorted(glob.glob("logs/funds/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    core = d.get("core_twr") or {}
    try:
        dd = 1 - float(core["twr"]) / float(core["twr_peak"])
    except Exception:  # noqa: BLE001
        dd = None
    print("rev", d.get("legs_revision"), "| 본 다리 낙폭", None if dd is None else f"{dd * 100:.2f}%",
          "| 노출", [leg.get("exposure") for leg in d.get("legs") or []])
PY
echo "== ETC · DYDX 구독 · 첫 봉"
grep -E '"(ETC_USDT|DYDX_USDT)"' "$LOG" | grep -E 'gate_ws_subscribed|live_stream_first|live_run_resumed|live_adopt|live_position_adopted|live_stop' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"|"symbol": "[A-Z_]+"|"timeframe": "[0-9a-z]+"' | paste -sd' ' | sed 's/ 2026-/\n2026-/g' | tail -n 12
echo "== 수수료 맞춤(맞춘 것 · 건너뛴 것)"
grep -oE '"event_type": "(live_fee_aligned|live_fee_align_skipped)"' "$LOG" | sort | uniq -c
grep 'live_fee_aligned' "$LOG" | grep -oE '"trade_id": "[a-f0-9]+"|"fee": "[^"]+"|"life_fee": "[^"]+"' | paste - - - | head -n 8
echo "== 판정 축 따라잡기 사건"
grep -oE '"event_type": "(live_decision_frame_stale|live_decision_catchup_failed|live_price_frame_stale)"' "$LOG" | sort | uniq -c
echo "== 감시 경보(10분)"
SINCE=$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M)
awk -v s="$SINCE" '$1 >= s' "$LOG" | grep 'run_watch_alarm' | grep -oE '"code": "[a-z_]+"|"detail": "[A-Z_]+' | paste - - | sort | uniq -c
echo "== 경고 · 오류(30분)"
grep -E '"level": "(error|warning|ERROR|WARNING)"' "$LOG" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -n 12
echo "== Traceback"; grep -c Traceback "$LOG"
rm -f "$LOG"
