#!/usr/bin/env bash
# 방금 청산이 **실제로 얼마를 먹었나** — 거래소 계좌 장부를 종류별로 가른다 (2026-09-19).
# "지금 다 닫으면" 카드가 예상값을 내려면 먼저 실제 구성(수수료·펀딩·실현손익)을 알아야 한다.
# 시크릿 없음 · 집계와 종류별 합만 찍는다.
set -uo pipefail
API=$(docker ps --filter "name=api_b" --filter "status=running" --format "{{.Names}}" | head -1)
[ -n "$API" ] || API=$(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}" | head -1)
echo "api: $API"
docker exec -i "$API" python - <<'PY'
import asyncio
import os
from collections import defaultdict
from decimal import Decimal

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음 — 이 컨테이너는 실계좌가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    book = await c.account_book(limit=200)
    by_type: dict[str, list[Decimal]] = defaultdict(list)
    for row in book:
        raw = row.get("change")
        if raw is None:
            continue
        by_type[str(row.get("type") or "?")].append(Decimal(str(raw)))
    print(f"장부 줄 {len(book)}개 · 종류 {len(by_type)}가지")
    print(f"{'종류':<10} {'건수':>5} {'합계':>14}")
    total = Decimal(0)
    for kind in sorted(by_type):
        s = sum(by_type[kind], Decimal(0))
        total += s
        print(f"{kind:<10} {len(by_type[kind]):>5} {s:>14.6f}")
    print(f"{'합계':<10} {'':>5} {total:>14.6f}")
    print()
    print("=== 최근 24줄 ===")
    for row in book[:24]:
        print(
            str(row.get("time"))[:19],
            f"{str(row.get('type') or '?'):<8}",
            f"{Decimal(str(row.get('change') or 0)):>12.6f}",
            str(row.get("contract") or "")[:12],
            str(row.get("text") or "")[:30],
        )


asyncio.run(main())
PY
