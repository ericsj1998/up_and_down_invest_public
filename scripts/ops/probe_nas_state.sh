#!/usr/bin/env bash
# 나스닥 엔진 다리 상태(읽기만) — 상태 요약 + 몫마다 한 줄(종류 · 계약 · 체결가 · 유령 · 손절 · 매도 주문).
#   사용: bash scripts/ops/remote.sh scripts/ops/probe_nas_state.sh
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
echo "== $(date -u +%H:%M:%SZ) $C"
docker exec "$C" python3 -c '
import json, glob
for f in glob.glob("logs/funds/engines/*NAS100*"):
    d = json.load(open(f))
    s = d.get("state", {})
    print("### 상태:", {k: v for k, v in s.items() if k not in ("carry", "grid")})
    print("grid:", s.get("grid"))
    print("carry:", s.get("carry"))
    for k, u in d.get("units", {}).items():
        print(k, u.get("kind"), "계약", u.get("contracts"), "체결", u.get("fill"), "계획", u.get("plan"), "유령", u.get("ghost"), u.get("ghost_reason"), "손절", u.get("stop_px"), "매도주문", bool(u.get("sell_id")), "판", u.get("sold"), "열림", str(u.get("opened"))[:16])
    print("그 밖 키:", [k for k in d if k not in ("state", "units")])
' 2>&1 | head -80
