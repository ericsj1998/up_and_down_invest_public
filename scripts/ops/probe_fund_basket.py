"""실계좌 펀드 바스켓 종목 — 수집기 · 프로브 목록을 맞출 때 (읽기 전용 · 값만).

bash scripts/ops/remote.sh scripts/ops/probe_fund_basket.py
"""

from updown.orchestration.report.funds import load_fund_snapshots

for snap in load_fund_snapshots():
    print(snap.fund_id, snap.market, len(snap.symbols), ",".join(sorted(snap.symbols)))
