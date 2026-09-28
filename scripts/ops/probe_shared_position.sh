#!/usr/bin/env bash
# T320 P0 — Gate **테스트넷**(서버 데모 계정)에서 한 포지션을 몫 둘로 나눠 쓸 수 있는지 잰다. 주문을 낸다(테스트넷만).
#
#     bash scripts/ops/remote.sh scripts/ops/probe_shared_position.sh
#
# 🔴 사용자 허가 뒤에만 돌린다(T320 D1 · 서버 쓰기). 라이브 키는 안 읽는다(`GATE_TESTNET_*` 만) · 테스트넷이 아니면 멈춘다.
# 🔴 데모 판이 안 쓰는 종목(기본 ARB_USDT)에서만 · 시작 때 그 종목 포지션 · 미결 · 조건부가 0 이 아니면 멈춘다.
# 🔴 끝나면(도중 실패해도) 이 탐침이 낸 주문 · 조건부를 거두고 포지션을 0 으로 닫는다.
#
# 잴 것:
#   ① 한 포지션(몫 A 2 + 몫 B 3 = 5 계약)에 **크기를 준** reduce-only 조건부 손절 둘(-2 · -3 · auto_size 없이)이 받아지나
#   ② 몫별 익절 지정가 넷(-1 -1 -1 -2 = 5)이 받아지나 · 하나 더(-1) 내면 REDUCE_ONLY_FAIL 인가(조건부가 그 합에 드나)
#   ③ 몫 A 만 나가기(A 익절 둘 · A 손절 취소 → reduce-only 시장가 -2)에 B 의 주문이 안 건드려지나
#   ④ 이미 참인 크기 준 조건부(-1)가 발동하면 **그 크기만** 줄어드나
set -u
SYMBOL="${1:-ARB_USDT}"
DEMO=$(docker ps --format '{{.Names}}' | grep -E 'api_demo' | head -1)
[ -n "$DEMO" ] || { echo "api_demo 컨테이너가 없다 — 멈춘다"; exit 1; }
echo "컨테이너 $DEMO · 종목 $SYMBOL"
docker exec -i -e PROBE_SYMBOL="$SYMBOL" "$DEMO" python - <<'PY' 2>&1 | grep -v '^{'
import asyncio
import json
import os
from decimal import ROUND_DOWN, Decimal

from updown.marketdata.gate.trade_client import SETTLE, GateApiError, GateTradeClient, price_text

SYMBOL = os.environ["PROBE_SYMBOL"]
TAG = "p0"


def show(step: str, **kv: object) -> None:
    print(json.dumps({"step": step, **{k: str(v) for k, v in kv.items()}}, ensure_ascii=False))


async def main() -> None:
    key = os.environ.get("GATE_TESTNET_API_KEY", "").strip()
    sec = os.environ.get("GATE_TESTNET_API_SECRET", "").strip()
    if not (key and sec):
        show("멈춤", why="GATE_TESTNET_* 없음")
        return
    c = GateTradeClient(key, sec)  # 기본 주소 = 테스트넷
    if not c.is_testnet:
        show("멈춤", why="테스트넷이 아니다")
        return
    req = c._request  # pyright: ignore[reportPrivateUsage]
    spec = await c.contract(SYMBOL)
    tick = Decimal(str(spec.get("order_price_round", "0.0001")))
    acct = await req("GET", f"/futures/{SETTLE}/accounts")
    if isinstance(acct, dict) and acct.get("in_dual_mode"):
        show("멈춤", why="양방향 모드 계정 — 한 방향 모드를 재는 탐침이다")
        return
    pos = await c.get_position(SYMBOL)
    size0 = int(str(pos.get("size", 0) or 0))
    orders0 = await c.list_orders(SYMBOL)
    stops0 = await c.list_stops(SYMBOL)
    show("시작", position=size0, open_orders=len(orders0), stops=len(stops0), tick=tick)
    if size0 != 0 or orders0 or stops0:
        show("멈춤", why="이 종목에 이미 포지션 · 주문이 있다 — 데모 판이 쓰는 종목일 수 있다")
        return
    ticker = await req("GET", f"/futures/{SETTLE}/tickers", params={"contract": SYMBOL})
    last = Decimal(str(ticker[0]["last"]))  # type: ignore[index]

    def px(mult: str) -> Decimal:
        return (last * Decimal(mult) / tick).quantize(Decimal(1), rounding=ROUND_DOWN) * tick

    made_orders: list[str] = []
    made_stops: list[str] = []

    async def order(name: str, size: int, *, price: Decimal | None = None, reduce: bool = False) -> dict:
        try:
            got = await c.place_order(
                SYMBOL, size, idempotency_key=f"{TAG}:{name}", price=price, reduce_only=reduce
            )
            if price is not None:
                made_orders.append(str(got.get("id", "")))
            show(name, ok=True, id=got.get("id"), status=got.get("status"), finish=got.get("finish_as"), size=size)
            return got
        except GateApiError as exc:
            show(name, ok=False, error=str(exc)[:160])
            return {}

    async def sized_stop(name: str, size: int, trigger: Decimal, rule: int) -> str:
        body = {
            "initial": {"contract": SYMBOL, "size": size, "price": "0", "tif": "ioc",
                        "reduce_only": True, "text": f"t-{TAG}-{name}"},
            "trigger": {"strategy_type": 0, "price_type": 0, "price": price_text(trigger),
                        "rule": rule, "expiration": 3600},
        }
        try:
            got = await req("POST", f"/futures/{SETTLE}/price_orders", body=body)
            sid = str(got.get("id", "")) if isinstance(got, dict) else ""
            made_stops.append(sid)
            show(name, ok=True, id=sid, size=size, trigger=price_text(trigger), rule=rule)
            return sid
        except GateApiError as exc:
            show(name, ok=False, error=str(exc)[:160])
            return ""

    async def state(step: str) -> int:
        p = await c.get_position(SYMBOL)
        o = await c.list_orders(SYMBOL)
        s = await c.list_stops(SYMBOL)
        size = int(str(p.get("size", 0) or 0))
        show(step, position=size, open_orders=[(x.get("text"), x.get("size")) for x in o],
             stops=[(x.get("initial", {}).get("text"), x.get("initial", {}).get("size")) for x in s])
        return size

    try:
        await order("A-en", 2)
        await order("B-en", 3)
        await state("① 두 몫 진입 뒤")
        sa = await sized_stop("A-sl", -2, px("0.90"), 2)
        await sized_stop("B-sl", -3, px("0.85"), 2)
        await state("① 크기 준 조건부 손절 둘 뒤")
        a1 = await order("A-tp1", -1, price=px("1.10"), reduce=True)
        a2 = await order("A-tp2", -1, price=px("1.20"), reduce=True)
        await order("B-tp1", -1, price=px("1.10"), reduce=True)
        await order("B-tp2", -2, price=px("1.20"), reduce=True)
        await state("② 몫별 익절 넷(합 5) 뒤")
        await order("over-1", -1, price=px("1.30"), reduce=True)
        await state("② 하나 더(합 6) 뒤 — 거절이어야")
        for got in (a1, a2):
            if got.get("id"):
                await c.cancel_order(str(got["id"]))
        if sa:
            await c.cancel_stop(sa)
        await order("A-exit", -2, reduce=True)
        await state("③ 몫 A 만 나간 뒤 — B 의 손절 · 익절 그대로여야 · 포지션 3")
        await sized_stop("fire-1", -1, px("0.99"), 1)  # 롱인데 '>=' 로 이미 참 — 곧 발동
        await asyncio.sleep(8)
        await state("④ 이미 참인 조건부(-1) 발동 뒤 — 포지션 2 여야")
    finally:
        for sid in made_stops:
            if sid:
                try:
                    await c.cancel_stop(sid)
                except GateApiError:
                    pass
        for oid in made_orders:
            if oid:
                try:
                    await c.cancel_order(oid)
                except GateApiError:
                    pass
        left = int(str((await c.get_position(SYMBOL)).get("size", 0) or 0))
        if left:
            await order("정리", -left, reduce=True)
        await state("끝 — 포지션 0 · 주문 0 이어야")


asyncio.run(main())
PY
