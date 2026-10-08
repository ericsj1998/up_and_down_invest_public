"""펀드 낙폭 교정 2차 — 10-08 17:00Z 이후 사람 주문 손익 · 수수료를 출금으로 재분류. 사람이 실행.

사용자 2026-10-09 04:30 KST "또 수동 매매했어 · 이제 진짜 안 할 거고 아예 손 떼려고 ·
다시 분류하는 스크립트 작동시키고 싶어".
1차(`fix_fund_drawdown_1009.py`)와 다른 점: 종목 · 시각 창을 손으로 적지 않고 **끝난 주문 text 로
주인을 가른다**(`t-` = 시스템 · 그 밖 = 사람 · 번호 못 찾음 = 모름 → 적용에서 빼고 줄을 찍는다).
창은 WINDOW_START 부터 지금까지.

## 사용 (WSL 셸 · 저장소 루트 · 정시 틱 직후 · 4H 경계(00 · 04 · 08 · 12 · 16 · 20Z) 피함)

수동 포지션이 전부 닫힌 뒤에 돌린다(열린 포지션이 있으면 적용을 거부한다).

  1. 계산만:
       bash scripts/ops/remote.sh scripts/ops/fix_fund_drawdown_1009b.py
  2. 적용(백업 `<파일>.bak_1009b`) — 표식을 만들고 다시 실행:
       bash scripts/ops/remote.sh scripts/ops/arm_fund_fix_1009b.sh
       bash scripts/ops/remote.sh scripts/ops/fix_fund_drawdown_1009b.py
  3. 바로 API 재기동:
       bash scripts/ops/remote.sh scripts/ops/restart_api_b_wait.sh
  4. 확인:
       bash scripts/ops/remote.sh scripts/ops/probe_fund_file_1009.sh
  되돌리기: 컨테이너 안 `<id>.json.bak_1009b` 를 원래 이름으로 복사하고 재기동.

## 산수 (1차와 같다)

    f = prod_i (1 + L_i / E_after_i)   (i = 손실이 흡수된 시간 단위 · E_after = 그 시간 끝 잔고)
    twr' = twr * f · contributed' = contributed - sum(L) · flows 에 "재분류 2차" 출금 줄
고점 그대로(교정 뒤 값이 더 높으면 그 값) · deepest 는 교정 뒤 지금 값 · core_twr · anchor 그대로.
펀드 equity 가 거래소 총액과 0.5 넘게 다르면 아직 흡수 안 된 변동이 있다 — 다음 정시 틱 뒤에.
키 · 계정 값은 찍지 않는다.
"""

import asyncio
import json
import os
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient

WINDOW_START = datetime(2026, 10, 8, 17, 0, tzinfo=UTC)
MARK = Path("/tmp/APPLY_FUND_FIX_B")
APPLY = MARK.exists()
NOTE = "수동 매매 손실 재분류 2차(사용자 2026-10-09 04:30) — 성과가 아니라 출금으로"


def d(v: object) -> Decimal:
    return Decimal(str(v))


def when(ts: object) -> datetime:
    return datetime.fromtimestamp(float(str(ts)), UTC)


async def manual_losses(c: GateTradeClient) -> list[tuple[datetime, Decimal, Decimal]]:
    """(시간 단위 시작, 사람 손실 합, 그 시간 마지막 줄 뒤 잔고) — 주인은 끝난 주문 text 로."""
    req = c._request  # pyright: ignore[reportPrivateUsage]
    orders = await req(
        "GET", f"/futures/{SETTLE}/orders", params={"status": "finished", "limit": "1000"}
    )
    texts = {str(o.get("id")): str(o.get("text") or "") for o in orders}
    rows = await req(
        "GET",
        f"/futures/{SETTLE}/account_book",
        params={
            "limit": "1000",
            "from": str(int(WINDOW_START.timestamp())),
            "to": str(int(datetime.now(UTC).timestamp())),
        },
    )
    by_hour: dict[datetime, list[tuple[datetime, Decimal, Decimal]]] = defaultdict(list)
    system = Decimal(0)
    unknown: list[str] = []
    for r in rows:
        typ = str(r.get("type"))
        if typ not in ("pnl", "fee"):
            continue
        text = str(r.get("text") or "")
        sym, _, oid = text.partition(":")
        owner = texts.get(oid.strip())
        chg = d(r.get("change") or 0)
        t = when(r["time"])
        if owner is None:
            unknown.append(f"{t:%H:%M:%SZ} {sym} {typ} {chg:+.3f} 주문 {oid[:12]}")
            continue
        if owner.startswith("t-"):
            system += chg
            continue
        hour = t.replace(minute=0, second=0, microsecond=0)
        by_hour[hour].append((t, chg, d(r.get("balance") or 0)))
    print(f"  창 안 시스템(t-) 손익 · 수수료 합 {system:+.3f} (재분류 안 함)")
    if unknown:
        print(f"  ⚠️ 주문 번호를 못 찾은 줄 {len(unknown)} — 적용에서 뺐다:")
        for line in unknown[:20]:
            print("    ", line)
    out: list[tuple[datetime, Decimal, Decimal]] = []
    for hour in sorted(by_hour):
        items = sorted(by_hour[hour], key=lambda x: (x[0], -x[2]))
        loss = sum((chg for _, chg, _ in items), Decimal(0))
        after = items[-1][2]
        out.append((hour, loss, after))
        print(
            f"  {hour:%m-%d %H}:00Z 사람 주문 줄 {len(items)} · 손실 {loss:+.3f} · 잔고 {after:.2f}"
        )
    return out


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("이 컨테이너는 실계좌가 아니다")
        return
    paths = sorted(Path("/app/logs/funds").glob("*.json")) or sorted(
        Path("logs/funds").glob("*.json")
    )
    if len(paths) != 1:
        print("펀드 파일이 하나가 아니다:", [p.name for p in paths])
        return
    path = paths[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    twr = data["twr"]
    if any(str(f.get("note", "")).startswith(NOTE) for f in twr.get("flows") or []):
        print("이미 2차 재분류 줄이 있다 — 두 번 적용하지 않는다")
        return
    old_twr, old_peak, old_deep = d(twr["twr"]), d(twr["twr_peak"]), d(twr["deepest"])
    old_eq, old_contrib = d(twr["equity"]), d(twr["contributed"])
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "🔴 적용" if APPLY else "계산만"
    print(f"=== {stamp} · {mode} · {path.name} · 창 {WINDOW_START:%m-%d %H:%M}Z ~")
    old_dd = (Decimal(1) - old_twr / old_peak) * 100
    print(
        f"지금 twr {old_twr:.6f} · 고점 {old_peak:.6f} · 낙폭 {old_dd:.2f}% · "
        f"deepest {old_deep * 100:.2f}%"
    )
    print(
        f"지금 equity {old_eq:.2f} · contributed {old_contrib:.2f} · "
        f"흐름 {len(twr.get('flows') or [])}줄"
    )

    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    print("=== 사람 주문 손실(거래소 자금 원장 · 끝난 주문 text 로 가름)")
    losses = await manual_losses(c)
    acc = await c.get_account()
    total_now = d(acc.get("total") or 0)
    positions = [p for p in await c.get_positions() if str(p.get("size", "0")) not in ("0", "")]
    print(
        f"거래소 총액 {total_now:.2f} · 펀드 equity {old_eq:.2f} · "
        f"차이 {total_now - old_eq:+.2f} · 열린 포지션 {len(positions)}"
    )
    blocked = False
    if positions:
        print("⛔ 열린 포지션이 있다 — 그 손익은 아직 실현 전이다. 전부 닫힌 뒤 돌린다.")
        blocked = True
    if abs(total_now - old_eq) > Decimal("0.5"):
        print("⚠️ 아직 흡수 안 된 변동이 있다 — 다음 정시 틱 뒤에 다시(교정 뒤 값이 바뀐다)")
        blocked = True
    if not losses:
        print("창 안에 사람 주문 손실이 없다 — 할 일 없음")
        return

    factor = Decimal(1)
    total_loss = Decimal(0)
    for _hour, loss, after in losses:
        factor *= Decimal(1) + (-loss) / after
        total_loss += loss
    new_twr = old_twr * factor
    new_peak = max(old_peak, new_twr)
    new_dd = (Decimal(1) - new_twr / new_peak) if new_peak > 0 else Decimal(0)
    new_deep = new_dd
    new_contrib = old_contrib + total_loss
    print("=== 교정")
    print(f"배수 f {factor:.6f} · 사람 손실 합 {total_loss:+.3f}")
    print(f"twr {old_twr:.6f} → {new_twr:.6f} · 고점 {old_peak:.6f} → {new_peak:.6f}")
    print(
        f"낙폭 {old_dd:.2f}% → {new_dd * 100:.2f}% · "
        f"deepest {old_deep * 100:.2f}% → {new_deep * 100:.2f}%"
    )
    print(f"contributed {old_contrib:.2f} → {new_contrib:.2f} · equity 그대로 {old_eq:.2f}")
    print("core_twr 는 안 건드린다 · anchor 도 그대로")

    if not APPLY:
        print("--- 계산만 했다. 적용은 머리말 2번(표식 → 다시 실행 → 3번 재기동).")
        return
    if blocked:
        print("⛔ 위 경고 때문에 적용하지 않았다. 표식은 남겨 둔다.")
        return
    backup = path.with_suffix(".json.bak_1009b")
    shutil.copy2(path, backup)
    flows = list(twr.get("flows") or [])
    for hour, loss, after in losses:
        flows.append(
            {
                "at": hour.isoformat(),
                "amount": str(loss.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)),
                "note": f"{NOTE} · 사람 주문 · 흡수 뒤 잔고 {after:.2f} · deepest 는 지금 값",
            }
        )
    twr["twr"] = str(new_twr)
    twr["twr_peak"] = str(new_peak)
    twr["deepest"] = str(new_deep)
    twr["contributed"] = str(new_contrib)
    twr["flows"] = flows
    tmp = path.with_suffix(".json.tmp_1009b")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    MARK.unlink(missing_ok=True)
    print(f"🔴 적용 완료 · 백업 {backup.name} · 이제 바로 3번(API 재기동)")


asyncio.run(main())
