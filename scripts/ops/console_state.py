"""콘솔이 부르는 `/exchange/state` 를 서버 안에서 직접 부른다 — 화면이 "—" 일 때 서버 쪽 답을 본다.

`bash scripts/ops/remote.sh scripts/ops/console_state.py`. 여기가 정상인데 화면이 비면 프론트 문제
(→ status.sh 의 browser traffic), 여기서 503 이면 거래소·키·IP 화이트리스트 문제다.
"""

import asyncio

from updown.apps.api import exchange


async def main() -> None:
    try:
        s = await exchange._state_fresh("BTC_USDT", "GATE")
        print(
            "STATE ok · broker=",
            s["balance"]["broker"],
            "available=",
            s["balance"]["available"],
            "testnet=",
            s["account"].get("testnet"),
            "acct_err=",
            s["account"].get("error"),
        )
    except Exception as exc:
        print("STATE ->", type(exc).__name__, str(exc)[:240])
    try:
        from updown.apps.api import rebalancer

        d = await rebalancer.defaults("GATE")
        print("DEFAULT_BASKET", [m["symbol"] for m in d["members"]], "missing=", d["missing"])
    except Exception as exc:
        print("DEFAULTS ->", type(exc).__name__, str(exc)[:160])


asyncio.run(main())
