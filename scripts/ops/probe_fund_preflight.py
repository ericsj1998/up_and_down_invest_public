"""펀드를 만들면 막힐 종목을 미리 잰다(읽기만 · 판을 띄우지 않는다).

`bash scripts/ops/remote.sh scripts/ops/probe_fund_preflight.py`
생성과 같은 문(`_start_blocker`)을
이번 분기 목록 · 지금 지갑 예산으로 종목마다 돌린다. 하나라도 막히면 생성이 통째로 되돌아간다.
"""

import asyncio
import json
import os
from decimal import Decimal
from types import SimpleNamespace

import psycopg

from updown.analysis.playbook.select import default_playbook, load_playbooks
from updown.apps.api import rebalancer as rb
from updown.apps.api import walkforward as wf
from updown.apps.api.exchange import _orders_adapter
from updown.common.config import Settings
from updown.common.db.session import create_engine, create_session_factory
from updown.orchestration.walkforward.live_runner import share_mode_faults


async def main() -> None:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as db:
        row = db.execute(
            "select quarter, legs_gate from universe_snapshots "
            "where status = 'ok' order by id desc limit 1"
        ).fetchone()
    if row is None:
        print("목록 없음")
        return
    raw = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    book = default_playbook()
    declared = next(b for b in load_playbooks() if b.playbook_id == book)
    scopes = {k: tuple(v) for k, v in raw.items() if k in declared.bundle}
    wanted = sorted({s for v in scopes.values() for s in v})
    legs = rb._legs_for(book, wanted, scopes)
    total = Decimal(str((await _orders_adapter().get_balance()).cash))
    share = total / Decimal(declared.slots)
    capital = total / Decimal(len(wanted))
    print("분기", row[0], "매매법", book, "총액", round(total, 2), "자리 예산", round(share, 2))
    print("종목", len(wanted), "· 몫", round(capital, 2))
    # 앱 기동과 같이 판 저장소를 붙인다(읽기만 — 한 종목 한 판 문이 본다).
    wf.attach_store(create_session_factory(create_engine(Settings().database_url)))  # type: ignore[call-arg]
    blocked = []
    sessions = 0
    for symbol in wanted:
        if rb._engine_leg(legs, symbol) is not None:
            continue
        sessions += 1
        name, lever = rb._member_terms(book, Decimal(6), None, legs, symbol)
        payload = rb._member_payload(
            symbol, share, lever, name, market="GATE", adopt_from=None, booked=min(share, capital)
        )
        if sessions == 1:
            print("예산 칸", payload.get("margin"), "· 사이징", payload.get("sizing_budget"))
        faults = share_mode_faults(
            SimpleNamespace(playbooks=wf.start_books(payload), limit_entry=False)
        )
        if faults:
            blocked.append(symbol)
            print("막힘", symbol, "러너 문", faults[:3])
        found = await wf._start_blocker(payload, None)
        if found is not None:
            blocked.append(symbol)
            print("막힘", symbol, "배율", lever, "문", found.gate, "·", str(found.why)[:170])
    print("세션 판", sessions, "· 막힘", len(blocked), blocked)


asyncio.run(main())
