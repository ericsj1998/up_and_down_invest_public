"""종목 순위 **탭별 응답과 걸린 시간** — api 컨테이너 안에서 직접 부른다 (읽기 전용).

    docker cp scripts/ops/probe_ranking_tabs.py updown-api_demo-1:/tmp/t.py
    docker exec updown-api_demo-1 python /tmp/t.py

HTTP 로 부르면 인증(401)에 막힌다 — 같은 프로세스 밖이라 캐시는 비어 있고, 그래서 첫 호출이
**캐시 없는 실측**이고 두 번째 호출이 **TTL 안 응답**이다.
"""

import asyncio
import time

from updown.apps.api import exchange as api


async def main() -> None:
    first = await api.ranking()
    groups = [g["key"] for g in first.get("groups", [])]
    print(f"거래소 {first.get('market')} · 탭 {groups} · 태그 {sorted(first.get('tags', {}))}")
    if first.get("note"):
        print(f"  note: {first['note']}")
    for key in groups:
        api._STATE_CACHE.entries.clear()  # pyright: ignore[reportPrivateUsage]
        t0 = time.perf_counter()
        body = await api.ranking(group=key)
        cold = time.perf_counter() - t0
        t1 = time.perf_counter()
        await api.ranking(group=key)
        warm = time.perf_counter() - t1
        rows = body["rows"]
        missing = [r["symbol"] for r in rows if r.get("missing")]
        tagged = {r["symbol"]: r["tags"] for r in rows if r.get("tags")}
        print(
            f"  [{key}] {len(rows)}줄 · 처음 {cold:.2f}s · TTL 안 {warm * 1000:.1f}ms · "
            f"거래소에 없음 {len(missing)} · 태그 {tagged}"
        )
        print(f"      {[r['symbol'] for r in rows][:30]}")


asyncio.run(main())
