"""T226 실측 — 원장 펀딩 누적(wf_trades.funding_paid) vs Gate 자금 원장(fund) 합.

`bash scripts/ops/remote.sh scripts/ops/probe_funding.py` — api 컨테이너 안 · 읽기 전용.
"""

import asyncio
import os
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient


def _open_trades() -> list[dict[str, object]]:
    url = os.environ.get("DATABASE_URL", "")
    eng = create_engine(url.replace("+asyncpg", "+psycopg"))
    with eng.connect() as conn:
        cols = {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    "select table_name, string_agg(column_name, ',') "
                    "from information_schema.columns "
                    "where table_name like 'wf_%' group by table_name"
                )
            )
        }
        run_table = next(
            (t for t, c in cols.items() if "symbol" in c.split(",") and t != "wf_trades"), None
        )
        print("wf tables:", sorted(cols), "· symbol in", run_table)
        if run_table is None:
            return []
        rows = conn.execute(
            text(
                f"select r.symbol, t.direction, t.opened_at, t.funding_paid, "
                f"t.funding_pct, t.closed_at "
                f"from wf_trades t join {run_table} r on r.id = t.run_id "
                "where t.opened_at > now() - interval '10 days' order by t.opened_at"
            )
        )
        return [
            dict(
                zip(
                    (
                        "symbol",
                        "direction",
                        "opened_at",
                        "funding_paid",
                        "funding_pct",
                        "closed_at",
                    ),
                    r,
                    strict=True,
                )
            )
            for r in rows
        ]


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음 — 이 컨테이너는 실계좌가 아니다")
        return
    trades = _open_trades()
    for t in trades:
        print(
            "  LEDGER",
            t["symbol"],
            t["direction"],
            "opened",
            t["opened_at"].strftime("%m-%d %H:%M") if t["opened_at"] else None,
            "closed",
            t["closed_at"].strftime("%m-%d %H:%M") if t["closed_at"] else "open",
            "funding_paid=",
            t["funding_paid"],
            "pct=",
            t["funding_pct"],
        )
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    book = await c.account_book(limit=300)
    fund = [r for r in book if str(r.get("type")) == "fund"]
    print(
        "BOOK rows",
        len(book),
        "fund rows",
        len(fund),
        "types",
        sorted({str(r.get("type")) for r in book}),
    )
    by: dict[str, Decimal] = defaultdict(Decimal)
    first: dict[str, datetime] = {}
    for r in fund:
        contract = str(r.get("text", "")).split(":")[0]
        ts = datetime.fromtimestamp(float(r.get("time", 0)), tz=UTC)
        by[contract] += Decimal(str(r.get("change", "0")))
        first[contract] = min(first.get(contract, ts), ts)
    for contract, total in sorted(by.items()):
        print(
            "  BOOK", contract, "fund sum=", total, "since", first[contract].strftime("%m-%d %H:%M")
        )
    # 열린 매매 구간만 — 원장은 opened_at 뒤의 정산만 붙인다
    for t in trades:
        if t["closed_at"] is not None or not t["opened_at"]:
            continue
        opened = t["opened_at"]
        window = sum(
            (
                Decimal(str(r.get("change", "0")))
                for r in fund
                if str(r.get("text", "")).split(":")[0] == t["symbol"]
                and datetime.fromtimestamp(float(r.get("time", 0)), tz=UTC) > opened
            ),
            Decimal(0),
        )
        print("  COMPARE", t["symbol"], "ledger=", t["funding_paid"], "book(opened 뒤)=", window)


asyncio.run(main())
