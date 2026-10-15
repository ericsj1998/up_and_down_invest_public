#!/usr/bin/env bash
# Gate 테스트넷(서버 데모 계정 · 페이크머니)에서 "같은 방향 격리 포지션이 따로 서나 · 손절도 따로 걸리나" 시험 (2026-10-16 사용자 요청)
#
#   bash scripts/ops/remote.sh scripts/ops/test_testnet_split.sh
#
# 순서: 모드 dual_plus(분할) → BTC_USDT 격리 롱 1계약 두 번 → 포지션 목록(pid) → 포지션마다 손절 → 정리(손절 취소 ·
# 포지션 닫기 · 모드 single 로 되돌림). 정리는 finally 에서 반드시 돈다. 실계좌 키는 쓰지 않는다(테스트넷 키 · 테스트넷 주소만).
DEMO=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "api_demo" | head -1)
echo "=== $(date -u +%H:%M:%SZ) · ${DEMO:-데모 컨테이너 없음}"
[ -z "$DEMO" ] && exit 1
docker exec -i "$DEMO" python - <<'PY' 2>&1 | grep -v "outbound_request"
import asyncio, json, os
from decimal import Decimal
from updown.marketdata.gate.trade_client import TESTNET_BASE_URL, GateApiError, GateTradeClient

CT = "BTC_USDT"
S = "/futures/usdt"


def short(x, n=600):
    return json.dumps(x, ensure_ascii=False, default=str)[:n]


async def call(c, method, path, *, params=None, body=None, label=""):
    try:
        got = await c._request(method, path, params=params, body=body)
        print(f"OK  {label or method + ' ' + path}: {short(got)}")
        return got
    except GateApiError as exc:
        print(f"ERR {label or method + ' ' + path}: {str(exc)[:400]}")
        return None


async def positions(c, tag):
    rows = await call(c, "GET", f"{S}/positions", params={"holding": "true"}, label=f"positions[{tag}]") or []
    mine = [r for r in rows if r.get("contract") == CT]
    print(f"--- {tag}: {CT} 줄 {len(mine)}")
    for r in mine:
        keep = {k: r.get(k) for k in ("pid", "size", "entry_price", "leverage", "lever", "mode", "pos_margin_mode", "margin", "liq_price")}
        print("    ", short(keep, 400))
    return mine


async def main():
    key, sec = os.environ.get("GATE_TESTNET_API_KEY", ""), os.environ.get("GATE_TESTNET_API_SECRET", "")
    assert key and sec, "테스트넷 키 없음"
    c = GateTradeClient(key, sec, base_url=TESTNET_BASE_URL)
    assert c.is_testnet
    a = await c.get_account()
    print("시작 모드", a.get("position_mode"), "enable_new_dual_mode=", a.get("enable_new_dual_mode"), "total=", a.get("total"))
    stops = []
    try:
        # ① 분할 모드로
        got = await call(c, "POST", f"{S}/set_position_mode", params={"position_mode": "dual_plus"}, label="모드→dual_plus(쿼리)")
        if got is None:
            got = await call(c, "POST", f"{S}/set_position_mode", body={"position_mode": "dual_plus"}, label="모드→dual_plus(본문)")
        a = await c.get_account()
        print("지금 모드", a.get("position_mode"))
        if a.get("position_mode") != "dual_plus":
            print("분할 모드로 못 바꿈 — 시험 중단")
            return
        # ② 격리 배율 3
        await call(c, "POST", f"{S}/positions/{CT}/leverage", params={"leverage": "3", "dual_side": "dual_long"}, label="배율 3(dual_long)")
        # ③ 격리 롱 두 번 — pid 없이
        for tag in ("a", "b"):
            await call(c, "POST", f"{S}/orders",
                       body={"contract": CT, "size": 1, "price": "0", "tif": "ioc", "text": f"t-split-{tag}", "pos_margin_mode": "isolated"},
                       label=f"격리 롱 {tag}")
            await positions(c, f"롱 {tag} 뒤")
        rows = await positions(c, "두 번 산 뒤")
        # ③-2 한 줄로 합쳐졌으면 pid=0(새 포지션?)으로 한 번 더 시험
        if len(rows) == 1:
            await call(c, "POST", f"{S}/orders",
                       body={"contract": CT, "size": 1, "price": "0", "tif": "ioc", "text": "t-split-c", "pos_margin_mode": "isolated", "pid": 0},
                       label="격리 롱 c(pid=0)")
            rows = await positions(c, "pid=0 시험 뒤")
        # ④ 포지션마다 손절(pid 지정)
        for r in rows:
            pid, size = r.get("pid"), int(r.get("size") or 0)
            trig = (Decimal(str(r.get("entry_price"))) * Decimal("0.9")).quantize(Decimal("0.1"))
            got = await call(c, "POST", f"{S}/price_orders",
                             body={"initial": {"contract": CT, "size": -size, "price": "0", "tif": "ioc", "reduce_only": True,
                                               "text": f"t-split-stop-{pid}", "pid": pid, "pos_margin_mode": "isolated"},
                                   "trigger": {"strategy_type": 0, "price_type": 0, "price": str(trig), "rule": 2}},
                             label=f"손절 pid={pid} size={-size} @ {trig}")
            if got and got.get("id"):
                stops.append(got["id"])
        po = await call(c, "GET", f"{S}/price_orders", params={"status": "open", "contract": CT}, label="열린 조건부") or []
        for p in po:
            ini = p.get("initial") or {}
            print("     조건부", p.get("id"), "trigger", (p.get("trigger") or {}).get("price"), "size", ini.get("size"), "pid", ini.get("pid"), "text", ini.get("text"))
    finally:
        print("=== 정리")
        po = await call(c, "GET", f"{S}/price_orders", params={"status": "open", "contract": CT}, label="정리 전 조건부") or []
        for p in po:
            await call(c, "DELETE", f"{S}/price_orders/{p.get('id')}", label=f"조건부 취소 {p.get('id')}")
        for _ in range(3):
            rows = await positions(c, "정리 중")
            if not rows:
                break
            for r in rows:
                size = int(r.get("size") or 0)
                if size:
                    await call(c, "POST", f"{S}/orders",
                               body={"contract": CT, "size": -size, "price": "0", "tif": "ioc", "reduce_only": True,
                                     "text": "t-split-x", "pid": r.get("pid"), "pos_margin_mode": "isolated"},
                               label=f"닫기 pid={r.get('pid')}")
        await call(c, "POST", f"{S}/set_position_mode", params={"position_mode": "single"}, label="모드→single")
        a = await c.get_account()
        rows = await positions(c, "끝")
        print("끝 모드", a.get("position_mode"), "· BTC 줄", len(rows), "· total", a.get("total"))
    await c.aclose()

asyncio.run(main())
PY
