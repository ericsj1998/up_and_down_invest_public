"""T841 실계좌 비용 대장 — 서버 api 컨테이너 안에서 도는 쪽(거래소 읽기만).

    bash scripts/ops/remote.sh scripts/ops/t841_fills.py > logs/t279/t841/fills.json

Gate 체결 기록(`my_trades_timerange` · 체결가 · 메이커 / 테이커 · 수수료)과 자금 원장 펀딩 줄을
2026-09-01 부터 지금까지 1,000 줄씩 받아 JSON 한 줄로 낸다.
키 · 비밀값은 찍지 않는다. 조회 사이 0.3 초 쉰다.
"""

import asyncio
import json
import os
import time

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient

FROM = 1788220800  # 2026-09-01T00:00:00Z


async def page(req, path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        rows = await req("GET", path, params={**params, "limit": "1000", "offset": str(off)})
        rows = rows or []
        out.extend(rows)
        if len(rows) < 1000 or off >= 50000:
            return out
        off += 1000
        await asyncio.sleep(0.3)


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print(json.dumps({"error": "no live keys"}))
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    now = int(time.time())
    out: dict = {"at": now}
    fills = []
    # my_trades_timerange 는 한 번에 구간이 길면 거절할 수 있어 30 일씩 나눈다
    t0 = FROM
    while t0 < now:
        t1 = min(t0 + 30 * 86400, now)
        try:
            fills += await page(
                c._request,
                f"/futures/{SETTLE}/my_trades_timerange",
                {"from": str(t0), "to": str(t1)},
            )
        except Exception as exc:
            out.setdefault("errors", []).append(f"fills {t0}: {type(exc).__name__}")
        t0 = t1
        await asyncio.sleep(0.3)
    out["fills"] = fills
    # 자금 원장도 30 일씩(한 번에 긴 구간은 400 을 낸다 · 11-13 실측)
    fund, t0 = [], FROM
    while t0 < now:
        t1 = min(t0 + 30 * 86400, now)
        try:
            fund += await page(
                c._request,
                f"/futures/{SETTLE}/account_book",
                {"type": "fund", "from": str(t0), "to": str(t1)},
            )
        except Exception as exc:
            out.setdefault("errors", []).append(f"fund {t0}: {type(exc).__name__}")
        t0 = t1
        await asyncio.sleep(0.3)
    out["fund_book"] = fund
    # remote.sh 가 '{' 로 시작하는 줄을 거르므로 표식을 앞에 붙인다
    print("T841JSON " + json.dumps(out, default=str))


asyncio.run(main())
