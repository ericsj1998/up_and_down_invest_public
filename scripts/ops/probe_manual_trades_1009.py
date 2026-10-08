"""10-07 배포 뒤 실계좌 체결이 누구 주문인가 — 수동(web · app) 대 시스템(`t-`) (읽기 전용).

`bash scripts/ops/remote.sh scripts/ops/probe_manual_trades_1009.py`.
사용자 2026-10-09 고백("친구 믿고 들어갔다가 100달러 언저리 손실")의 흔적을 거래소 장부에서 찾는다:
  ① 자금 원장(account_book) 10-07 11:00Z ~ : pnl · fee · fund · dnw 합과 줄 — 어느 종목에서 얼마
  ② 끝난 주문(전 종목) 중 text 가 `t-` 가 아닌 것 = 사람이 거래소 화면에서 낸 주문
  ③ 닫힌 포지션(position_close) 10-07 ~
키 · 계정 값은 찍지 않는다. 숫자 · 종목 · 시각 · text 꼬리만.
2026-10-08 실측: BTC 수동 롱 7회전(14:19 ~ 15:02Z) pnl -37.4 · 수수료 -74.3 = -111.8 ·
시스템 주문은 2건(TRB 청산 · ATOM 진입)뿐.
"""

import asyncio
import os
from collections import defaultdict
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient

SINCE = datetime(2026, 10, 7, 11, 0, tzinfo=UTC).timestamp()


def when(t: object) -> str:
    return datetime.fromtimestamp(float(str(t)), UTC).strftime("%m-%d %H:%MZ")


def num(v: object) -> float:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return 0.0


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("이 컨테이너는 실계좌가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    req = c._request  # pyright: ignore[reportPrivateUsage]
    now = str(int(datetime.now(UTC).timestamp()))

    print("=== ① 자금 원장 10-07 11:00Z ~ (type 별 합 · 종목별 pnl+fee)")
    rows = await req(
        "GET",
        f"/futures/{SETTLE}/account_book",
        params={"limit": "1000", "from": str(int(SINCE)), "to": now},
    )
    by_type: dict[str, float] = defaultdict(float)
    by_sym: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        typ = str(r.get("type"))
        chg = num(r.get("change"))
        by_type[typ] += chg
        text = str(r.get("text") or "")
        sym = text.split(":")[0] if typ in ("pnl", "fee", "fund", "point_fee") else "-"
        by_sym[sym][typ] += chg
    print("줄", len(rows), "· type 합", {k: round(v, 3) for k, v in sorted(by_type.items())})
    for sym, d in sorted(by_sym.items(), key=lambda kv: sum(kv[1].values())):
        total = round(sum(d.values()), 3)
        print("  ", sym, {k: round(v, 3) for k, v in sorted(d.items())}, "합", total)
    print("  줄(시각 · type · change · balance · text 앞 24)")
    for r in sorted(rows, key=lambda x: num(x.get("time"))):
        if str(r.get("type")) in ("pnl", "dnw", "fund") or abs(num(r.get("change"))) >= 1:
            print(
                "   ",
                when(r["time"]),
                str(r.get("type")).ljust(5),
                f"{num(r.get('change')):+9.3f}",
                "bal",
                f"{num(r.get('balance')):8.2f}",
                str(r.get("text") or "")[:24],
            )

    print("=== ② 끝난 주문(전 종목 · 10-07 11:00Z ~) — text 가 t- 아닌 것 = 사람 주문")
    try:
        orders = await req(
            "GET", f"/futures/{SETTLE}/orders", params={"status": "finished", "limit": "500"}
        )
    except Exception as exc:
        print("  전 종목 목록 실패:", str(exc)[:100])
        orders = []
    hit = [o for o in orders if num(o.get("create_time")) >= SINCE]
    kinds: dict[str, int] = defaultdict(int)
    print("  끝난 주문", len(orders), "· 창 안", len(hit))
    for o in sorted(hit, key=lambda x: num(x.get("create_time"))):
        text = str(o.get("text") or "")
        kind = "system" if text.startswith("t-") else (text.split("-")[0] or "(빈)")
        kinds[kind] += 1
        if kind != "system":
            print(
                "   ",
                when(o["create_time"]),
                o.get("contract"),
                "size",
                o.get("size"),
                "fill",
                o.get("fill_price"),
                "close",
                o.get("is_close"),
                "reduce",
                o.get("is_reduce_only"),
                "text",
                text[:20],
                "finish",
                o.get("finish_as"),
            )
    print("  TEXT_KINDS", dict(kinds))

    print("=== ③ 닫힌 포지션(position_close) 10-07 ~")
    try:
        closes = await req(
            "GET",
            f"/futures/{SETTLE}/position_close",
            params={"limit": "100", "from": str(int(SINCE))},
        )
    except Exception as exc:
        print("  실패:", str(exc)[:100])
        closes = []
    for p in sorted(closes, key=lambda x: num(x.get("time"))):
        print(
            "   ",
            when(p["time"]),
            p.get("contract"),
            "side",
            p.get("side"),
            "pnl",
            p.get("pnl"),
            "pnl_pnl",
            p.get("pnl_pnl"),
            "fee",
            p.get("pnl_fee"),
            "fund",
            p.get("pnl_fund"),
            "max_size",
            p.get("max_size"),
            "accum",
            p.get("accum_size"),
            "text",
            str(p.get("text") or "")[:16],
        )


asyncio.run(main())
