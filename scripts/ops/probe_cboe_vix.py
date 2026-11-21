"""서버(api 컨테이너)에서 CBOE VIX 일별 이력 CSV 를 받을 수 있나 — 읽기만(바깥 GET 한 번).

    bash scripts/ops/remote.sh scripts/ops/probe_cboe_vix.py

출력: 상태 · 바이트 · 행 수 · 마지막 행(날짜 · 종가). 시크릿 · 계좌 정보는 안 찍는다.
"""

import time
import urllib.request

URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
t0 = time.time()
try:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", "replace")
        status = r.status
    rows = [ln for ln in body.strip().splitlines() if ln[:1].isdigit()]
    last = rows[-1] if rows else "-"
    took = time.time() - t0
    print(f"상태 {status} · {len(body):,} 바이트 · 행 {len(rows):,} · 마지막 {last} · {took:.1f}초")
except Exception as exc:
    print(f"실패 {type(exc).__name__}: {str(exc)[:160]} · {time.time() - t0:.1f} 초")
