"""종목 순위가 **어디서 느린가** — 단계별 시간 (읽기 전용 · api 컨테이너 안).

    docker cp scripts/ops/probe_ranking_timing.py updown-api_demo-1:/tmp/t.py
    docker exec updown-api_demo-1 python /tmp/t.py
    bash scripts/ops/remote.sh scripts/ops/probe_ranking_timing.py

사용자: *"종목 순위 띄우는데 좀 오래 걸리긴 한다."* 추측으로 고치지 않는다 — 세 단계를 따로 잰다:

    ① 전 종목 24시간 요약 (한 번 · `ticker_stats`)
    ② 종목마다 호가창 (`probe_book` · "나갈 수 있나")
    ③ 종목마다 15분봉 넷 (`_recent` · "최근 1시간 변동성")
"""

import asyncio
import time
from decimal import Decimal

from updown.apps.api import exchange as api
from updown.orchestration.liquidity import probe_book


async def main() -> None:
    t0 = time.perf_counter()
    name, quotes = await api._board_market(None)  # pyright: ignore[reportPrivateUsage]
    if name is None or quotes is None:
        print("연결된 거래소가 없다")
        return
    tracked = api._universe(name)  # pyright: ignore[reportPrivateUsage]
    t1 = time.perf_counter()
    stats = await quotes.ticker_stats()
    t2 = time.perf_counter()
    print(f"거래소 {name} · 종목 {len(tracked)}개 · 요약 {len(stats)}줄")
    print(f"  우주 만들기        {t1 - t0:6.2f}s")
    print(f"  ① 전 종목 요약     {t2 - t1:6.2f}s")
    try:
        orders = api._orders_adapter(name)  # pyright: ignore[reportPrivateUsage]
        await asyncio.gather(
            *(probe_book(orders, api._instrument(s, name), Decimal(0)) for s in tracked),  # pyright: ignore[reportPrivateUsage]
            return_exceptions=True,
        )
        t3 = time.perf_counter()
        each = (t3 - t2) / max(1, len(tracked))
        print(f"  ② 호가창 x{len(tracked):<3d}      {t3 - t2:6.2f}s  (종목당 {each:.2f}s)")
    except Exception as exc:
        t3 = time.perf_counter()
        print(f"  ② 호가창           못 함: {str(exc)[:80]}")
    await asyncio.gather(
        *(api._recent(quotes, s, name) for s in tracked),  # pyright: ignore[reportPrivateUsage]
        return_exceptions=True,
    )
    t4 = time.perf_counter()
    each = (t4 - t3) / max(1, len(tracked))
    print(f"  ③ 15분봉 x{len(tracked):<3d}      {t4 - t3:6.2f}s  (종목당 {each:.2f}s)")
    print(f"  합                 {t4 - t0:6.2f}s")


asyncio.run(main())
