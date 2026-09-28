#!/usr/bin/env bash
# Gate **테스트넷** 무기한 계약 목록(읽기 전용) — T320 P0 탐침 종목을 데모 바스켓 밖에서 고르려고.
#
#     bash scripts/ops/remote.sh scripts/ops/probe_testnet_contracts.sh
#
# 읽기만 한다(테스트넷 키로 서명 · 라이브 키 안 읽음) · 이름 · 계약 승수 · 최종가만 찍는다.
set -u
DEMO=$(docker ps --format '{{.Names}}' | grep -E 'api_demo' | head -1)
[ -n "$DEMO" ] || { echo "api_demo 컨테이너가 없다"; exit 1; }
docker exec -i "$DEMO" python - <<'PY' 2>&1 | grep -v '^{'
import asyncio
import os

from updown.marketdata.gate.trade_client import SETTLE, GateTradeClient


async def main() -> None:
    key = os.environ.get("GATE_TESTNET_API_KEY", "").strip()
    sec = os.environ.get("GATE_TESTNET_API_SECRET", "").strip()
    c = GateTradeClient(key, sec)  # 기본 주소 = 테스트넷
    assert c.is_testnet
    rows = await c._request("GET", f"/futures/{SETTLE}/contracts")  # pyright: ignore[reportPrivateUsage]
    names = sorted(
        (str(r["name"]), str(r.get("quanto_multiplier")), str(r.get("last_price")))
        for r in rows  # type: ignore[union-attr]
        if not r.get("in_delisting")
    )
    print(len(names), "계약")
    for n, m, p in names:
        print(n, m, p)


asyncio.run(main())
PY
