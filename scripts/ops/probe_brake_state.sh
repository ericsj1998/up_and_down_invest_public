#!/usr/bin/env bash
# T357 — 실계좌 펀드 낙폭 브레이크가 지금 걸려 있나 (읽기 전용 · 값만 · 2026-10-04).
#   본 다리 낙폭 = 1 − core_twr.twr / core_twr.twr_peak · 다리 문턱(0.055)을 넘으면 신규 크기 x0.25 상태다.
#   bash scripts/ops/remote.sh scripts/ops/probe_brake_state.sh
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다 — 펀드 파일에서 낙폭 · 다리 문 값만 뽑는다.
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tAc"
echo "=== 전 판 정지 스위치(app_settings.live_entries_halted)"
$Q "select coalesce(max(value), '(없음)') || ' · 바뀐 시각 ' || coalesce(to_char(max(updated_at) at time zone 'Asia/Seoul','MM-DD HH24:MI KST'), '-') from app_settings where key='live_entries_halted'"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 펀드 파일 — 낙폭 · 다리 문(컨테이너 ${API})"
docker exec -i "$API" python - <<'PY'
import glob, json, os
paths = sorted(glob.glob("/app/logs/funds/*.json")) or sorted(glob.glob("logs/funds/*.json"))
print("파일", len(paths))
for p in paths:
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(os.path.basename(p), "읽기 실패", type(e).__name__)
        continue
    def dd(x):
        try:
            return 1 - float(x["twr"]) / float(x["twr_peak"])
        except Exception:  # noqa: BLE001
            return None
    tw, core = d.get("twr") or {}, d.get("core_twr") or {}
    print("--", os.path.basename(p)[:12], "| 이름", d.get("name"), "| legs_revision", d.get("legs_revision"),
          "| anchor.mode", (d.get("anchor") or {}).get("mode"), "| 멤버", len(d.get("members") or []))
    print("   펀드 twr", tw.get("twr"), "peak", tw.get("twr_peak"), "→ 낙폭", None if dd(tw) is None else f"{dd(tw) * 100:.2f}%")
    print("   본 다리 twr", core.get("twr"), "peak", core.get("twr_peak"), "→ 낙폭", None if dd(core) is None else f"{dd(core) * 100:.2f}%")
    for leg in d.get("legs") or []:
        keep = {k: leg.get(k) for k in ("attribution", "drawdown_brake", "halt_dd_at", "isolated", "drawdown_isolated", "entry_limit", "notional_cap", "peer_open_max") if k in leg}
        print("   다리", json.dumps(keep, ensure_ascii=False)[:260])
    for k in ("twr", "core_twr"):
        x = d.get(k) or {}
        extra = {kk: x.get(kk) for kk in x if kk not in ("twr", "twr_peak")}
        if extra:
            print("  ", k, "그 밖의 칸", json.dumps(extra, ensure_ascii=False, default=str)[:300])
PY
echo "=== 컨테이너 로그(기동 뒤) — 진입 문 사건(브레이크로 줄인 것 · 막은 이유)"
docker logs "$API" 2>&1 | grep -oE '"event": "(session_entry_gate_fit|session_entry_gate_held|fund_gate_attached|fund_legs_refreshed|fund_legs_refresh_failed|live_breaker_tripped|live_start_entries_halted)"' | sort | uniq -c | sort -rn
docker logs "$API" 2>&1 | grep -E 'session_entry_gate_(fit|held)' | grep -oE '"(by|why|reason)": "[^"]*"' | sort | uniq -c | sort -rn | head -20
echo "=== 컨테이너 기동 시각"
docker inspect -f '{{.State.StartedAt}}' "$API"
