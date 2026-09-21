"""화면이 RUN 하나에서 실제로 보여 주는 매매 목록 (읽기 전용 · api 컨테이너 안).

    bash scripts/ops/remote.sh scripts/ops/probe_run_view.py

🔴 **세션 화면은 예열 걸음의 과거 매매도 같이 보여 준다.** 그 매매들은 `wf_trades` 에
안 남는다(라이브 매매만 남는다) — 그래서 DB 로 "닫힌 매매 없음" 을 확인해 놓고도
화면에는 '손절' 이 줄줄이 보일 수 있다. 그 둘을 나란히 찍어 어느 쪽을 본 것인지 가른다.
"""

import json
import os
import urllib.request

BASE = "http://127.0.0.1:8000"
TOKEN = os.environ.get("AUTH_TEST_BYPASS", "")


def get(path: str) -> object:
    req = urllib.request.Request(f"{BASE}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def main() -> None:
    try:
        sessions = get("/api/walkforward/sessions")
    except Exception as exc:
        print("세션 목록을 못 읽음:", str(exc)[:160])
        return
    rows = sessions if isinstance(sessions, list) else (sessions or {}).get("sessions") or []
    print(f"=== 화면이 보는 RUN {len(rows)}개")
    for r in rows[:25]:
        if not isinstance(r, dict):
            continue
        print(
            f"  {r.get('key') or r.get('run_key')} | {r.get('symbol')} | {r.get('playbook')} "
            f"| 보유 {r.get('open_count', r.get('holding'))} | 매매 {r.get('trade_count', '?')}"
        )

    keys = [
        (r.get("key") or r.get("run_key"), r.get("symbol"))
        for r in rows
        if isinstance(r, dict) and (r.get("key") or r.get("run_key"))
    ]
    print("\n=== 각 RUN 의 매매 목록에서 결과별 건수 (예열 포함 — 화면이 보여 주는 그대로)")
    for key, sym in keys[:20]:
        try:
            state = get(f"/api/walkforward/state/{key}")
        except Exception as exc:
            print(f"  {sym} {key} -> 못 읽음: {str(exc)[:70]}")
            continue
        trades = (state or {}).get("trades") or (state or {}).get("ledger", {}).get("trades") or []
        if not isinstance(trades, list):
            continue
        kinds: dict[str, int] = {}
        last = ""
        for t in trades:
            if not isinstance(t, dict):
                continue
            k = str(t.get("outcome", "?"))
            kinds[k] = kinds.get(k, 0) + 1
            when = str(t.get("closed_at") or t.get("opened_at") or "")[:16]
            if when > last:
                last = when
        if kinds:
            shown = " · ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1]))
            print(f"  {sym:12s} 매매 {len(trades):4d} | {shown} | 마지막 {last}")


main()
