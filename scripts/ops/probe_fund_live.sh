#!/usr/bin/env bash
# 라이브 펀드가 **선언대로 돌고 있나** — 메모리의 펀드·세션·진입 문을 직접 들여다본다 (T286 점검).
# 파일(probe_fund_rules)은 "저장된 값" 이고 이것은 "지금 도는 값" 이다 — 둘이 갈라질 수 있는 자리가 배선이다.
# 시크릿 없음 · 종목·자리·배율·문 상태만.
set -uo pipefail
API=$(docker ps --filter "name=api_b" --filter "status=running" --format "{{.Names}}" | head -1)
[ -n "$API" ] || API=$(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}" | head -1)
echo "api: $API"
docker exec -i "$API" python - <<'PY'
from decimal import Decimal

from updown.apps.api.rebalancer import FUNDS
from updown.apps.api.walkforward import SESSIONS

if not FUNDS:
    print("!! 메모리에 펀드가 없다 (재기동 직후면 복원 중일 수 있다)")
for fid, f in FUNDS.items():
    eng = f.coordinator.engine
    print(f"=== {fid} · {f.label}")
    print(f"  매매법 {f.playbook} · 시장 {f.market} · 배분 {f.weight_mode}")
    print(f"  자리 {f.slots} · 배율 {f.leverage} · 상한 {f.notional_cap} · 줄여서 {f.notional_fit}")
    print(f"  브레이크 {f.drawdown_brake}")
    print(f"  총자본 {eng.balance} · TWR {eng.twr_return:+.4f} · 낙폭 {eng.ledger.drawdown_pct:.2f}%")
    print(f"  바스켓 {[m.symbol for m in eng.basket.members]}")
    print(f"  멤버 세션 {len(f.handles)}개")
    gates = set()
    for sym, handle in sorted(f.handles.items()):
        s = SESSIONS.get(handle)
        if s is None:
            print(f"    {sym:<12} !! 세션 없음 (handle {handle})")
            continue
        sess = s.session
        g = sess.entry_gate
        gates.add(id(g))
        led = sess.ledger
        open_n = sum(1 for r in led.records if r.outcome.name == "OPEN")
        print(
            f"    {sym:<12} 예산 {led.margin_budget} · 배율 {led.leverage} · "
            f"기록 {len(led.records)} (보유 {open_n}) · 문 {'있음' if g else '🔴 없음'}"
        )
    print(f"  문 객체 수 {len(gates)} (1 이어야 한다 — 전 세션이 같은 문을 봐야 상한·정지가 공유된다)")
    for g in (getattr(p.session, "entry_gate", None) for p in []):  # noqa: B007
        pass
    # 문 자체를 들여다본다 (첫 세션 기준)
    first = next(iter(f.handles.values()), None)
    g = SESSIONS[first].session.entry_gate if first in SESSIONS else None
    if g is not None:
        dd = None if g.drawdown is None else g.drawdown()
        print(
            f"  문: 자리 {g.slots} · 정지 {g.halt_after_stops} · 상한 {g.notional_cap} · "
            f"줄여서 {g.notional_fit} · 하한 {g.min_grant} · 문턱 {g.brake_at} x {g.brake_scale}"
        )
        print(f"  문이 읽는 지금 낙폭: {dd}")
        if dd is not None:
            want = Decimal(str(f.leverage))
            print(f"  지금 4x 를 요청하면: {g.grant(__import__('datetime').datetime.now(__import__('datetime').UTC), want)}")
PY
