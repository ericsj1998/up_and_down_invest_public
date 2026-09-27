"""Gate 자금 원장(account_book) 모양 프로브 (읽기 전용) — api 컨테이너 안에서 돈다.

`bash scripts/ops/remote.sh scripts/ops/probe_account_book.py`.
리포트 계좌 총액 그래프(기간별 · 넣은 돈 · 번 돈 · 잃은 돈)를 장부로 그릴 수 있는지 본다:
행에 변동 후 잔고(`balance`)가 있나 · 입출금(`dnw`)이 얼마나 과거까지 나오나 · 30일 행 수 ·
`from`/`to`/`offset` 이 먹나. 값은 요약만 — 키는 안 찍는다.
2026-09-27 실측: `balance` 있음 · 입출금 2줄(합 366.58) · `from` 만 주면 빈 목록 · 180일 넘으면 400.
"""

import asyncio
import os
import time
from collections import Counter
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient


def when(t: object) -> str:
    return datetime.fromtimestamp(float(str(t)), UTC).strftime("%Y-%m-%d %H:%M")


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    path = f"/futures/{SETTLE}/account_book"
    last = await c._request("GET", path, params={"limit": "3"})  # pyright: ignore[reportPrivateUsage]
    print("KEYS", sorted(last[0].keys()) if last else "빈 목록")
    dnw = await c._request("GET", path, params={"limit": "1000", "type": "dnw"})  # pyright: ignore[reportPrivateUsage]
    print("DNW 행", len(dnw), "· 합", round(sum(float(r["change"]) for r in dnw), 2))
    if dnw:
        print("DNW 가장 옛 ·", when(dnw[-1]["time"]), "· 가장 새 ·", when(dnw[0]["time"]))
    old = await c._request(  # pyright: ignore[reportPrivateUsage]
        "GET",
        path,
        params={"limit": "1000", "type": "dnw", "from": str(int(time.time()) - 179 * 86400)},
    )
    print("DNW from=179일 전 행", len(old), "· 합", round(sum(float(r["change"]) for r in old), 2))
    now = int(time.time())
    total, off, kinds = 0, 0, Counter()
    first_t = None
    while True:
        page = await c._request(  # pyright: ignore[reportPrivateUsage]
            "GET",
            path,
            params={
                "limit": "1000",
                "from": str(now - 30 * 86400),
                "to": str(now),
                "offset": str(off),
            },
        )
        total += len(page)
        kinds.update(str(r.get("type")) for r in page)
        if page:
            first_t = page[-1]["time"]
        if len(page) < 1000 or off > 20000:
            break
        off += 1000
    print("30일 행", total, "· 종류", dict(kinds), "· 가장 옛", when(first_t) if first_t else "—")


asyncio.run(main())
