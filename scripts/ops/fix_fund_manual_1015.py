# ruff: noqa: E501, RUF002
"""펀드 원장 교정 — 10-09 교정 뒤 생긴 **수동 매매 손익**을 성과가 아니라 입출금(흐름)으로 재분류.

사용자 2026-10-15 22시 KST *"내 매매 기록은 한번 싹 정리해줘. 내가 수동으로 이것저것 했는데 그거 다
빼고싶어"* · 22:3x *"그렇게 진행해줘"*. 10-09 교정(`fix_fund_drawdown_1009.py` · 그때는 BTC 창 둘)과 같은
산수를 쓰되, 이번엔 **주문 이름으로** 매매법 · 사람을 가른다(창 · 종목을 손으로 고르지 않는다).
1.40.2 부터는 앵커가 매 틱 자동으로 같은 일을 한다 — 이 스크립트는 그 앞의 지난 몫만 고친다.

## 사용 (WSL 셸 · 저장소 루트 · 정시 틱 직후 · 4H 경계 00 · 04 · 08 · 12 · 16 · 20Z 직후는 피함)

  1. 계산만 (파일 안 건드림 · 숫자 · 모름 줄 확인):
       bash scripts/ops/remote.sh scripts/ops/fix_fund_manual_1015.py
  2. 적용 (백업 `<파일>.bak_1015` 남김) — 컨테이너 표식으로 켠다:
       bash scripts/ops/remote.sh scripts/ops/mark_fund_fix_1015.sh
       bash scripts/ops/remote.sh scripts/ops/fix_fund_manual_1015.py
  3. 다음 정시 틱 전에 API 재기동 · 또는 배포(메모리가 파일을 다시 읽는다 · 그 전에 틱이 돌면 메모리가 덮어쓴다).
  되돌리기: 컨테이너 안 `/app/logs/funds/<id>.json.bak_1015` 를 원래 이름으로 복사하고 재기동.

## 가르기 (자금 원장 `account_book` 의 pnl · fee · fund 줄)

- 펀드 종목이 **아닌** 계약(사람만 매매한 계약)의 줄 → 사람.
- 펀드 종목 계약의 pnl · fee 줄 → 줄의 주문 번호(`계약:번호`)로 끝난 주문을 찾아:
  `t-…` = 매매법 · `ao-{조건부 id}` = 끝난 조건부의 원래 이름이 `t-…` 면 매매법(우리 손절) 아니면 사람 ·
  그 밖(`web…` · `app…` …) = 사람 · 못 찾음 = **모름(재분류 안 함 · 찍기만)**.
- 펀드 종목의 펀딩 줄은 매매법으로 둔다(사람 포지션은 몇 시간짜리라 펀딩이 거의 없다).
- 1.40.2 앵커가 이미 흐름으로 적은 시각(`매매법 밖 주문(수동)` 흐름) 뒤의 줄은 안 센다(두 번 세지 않는다).

## 산수 (10-09 와 같다)

앵커 = 거래소 계좌 총액이라 매시 틱이 그 시간의 사람 손익 L 을 성과로 흡수했다. L 을 흐름으로 봤다면 그 시간
수익률은 (E − L) / E_before 였어야 하므로 교정 배수 f = Π_h (1 − L_h / E_h) (h = 사람 줄이 든 시간 ·
E_h = 그 시간 끝 틱의 계좌 잔고 = 그 시각 앞 마지막 줄의 balance). twr' = twr · f · contributed' =
contributed + Σ L(손실은 출금 · 이익은 입금) · flows 에 시간마다 한 줄. 고점은 max(옛 고점, twr') · deepest 는
교정 뒤 지금 값(원 경로는 저장돼 있지 않다). 브레이크 원장(`core_twr`) · anchor 는 안 건드린다.
키 · 계정 값은 찍지 않는다.
"""

import asyncio
import json
import os
import shutil
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient

CUTOFF = datetime(2026, 10, 8, 17, 50, tzinfo=UTC)
"""10-09 2차 교정이 덮은 마지막 사람 줄(10-09 02:49 KST · 흡수 뒤 잔고 367.72) 뒤."""
MARK = Path("/tmp/APPLY_FUND_FIX_1015")
APPLY = MARK.exists()
NOTE = "수동 매매 손익 재분류(사용자 2026-10-15) — 성과가 아니라 입출금으로"
AUTO_NOTE = "매매법 밖 주문(수동)"


def d(v: object) -> Decimal:
    return Decimal(str(v or 0))


def when(ts: object) -> datetime:
    return datetime.fromtimestamp(float(str(ts)), UTC)


def kst(t: datetime) -> str:
    return (t + timedelta(hours=9)).strftime("%m-%d %H:%M")


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
    members = {str(m["symbol"]) for m in data["basket"]["members"]}
    twr = data["twr"]
    flows0 = list(twr.get("flows") or [])
    if any(str(f.get("note", "")).startswith(NOTE) for f in flows0):
        print("이미 재분류 줄이 있다 — 두 번 적용하지 않는다")
        return
    auto = [
        datetime.fromisoformat(str(f["at"]))
        for f in flows0
        if str(f.get("note", "")).startswith(AUTO_NOTE)
    ]
    until = min(auto) if auto else datetime.now(UTC)
    old_twr, old_peak, old_deep = d(twr["twr"]), d(twr["twr_peak"]), d(twr["deepest"])
    old_eq, old_contrib = d(twr["equity"]), d(twr["contributed"])
    old_dd = (Decimal(1) - old_twr / old_peak) * 100
    print(
        f"=== {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} · {'🔴 적용' if APPLY else '계산만'} · {path.name}"
    )
    print(
        f"지금 twr {old_twr:.6f} · 고점 {old_peak:.6f} · 낙폭 {old_dd:.2f}% · deepest {old_deep * 100:.2f}%"
    )
    print(f"지금 equity {old_eq:.2f} · contributed {old_contrib:.2f} · 흐름 {len(flows0)}줄")
    print(
        f"재분류 구간 {kst(CUTOFF)} ~ {kst(until)} KST"
        + (" (그 뒤는 1.40.2 앵커가 이미 흐름으로)" if auto else "")
    )

    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    req = c._request  # pyright: ignore[reportPrivateUsage]
    rows = await req(
        "GET",
        f"/futures/{SETTLE}/account_book",
        params={
            "limit": "1000",
            "from": str(int(CUTOFF.timestamp())),
            "to": str(int(datetime.now(UTC).timestamp())),
        },
    )
    if len(rows) >= 1000:
        print("⛔ 자금 원장 줄이 1000 이상 — 잘렸을 수 있다. 멈춘다")
        return
    orders = await c.list_orders(None, status="finished", limit=1000)
    texts = {str(o.get("id")): str(o.get("text") or "") for o in orders}
    stops = await req(
        "GET", f"/futures/{SETTLE}/price_orders", params={"status": "finished", "limit": "1000"}
    )
    stop_texts = {
        str(s.get("id_string") or s.get("id")): str((s.get("initial") or {}).get("text") or "")
        for s in stops
    }
    book = sorted(rows, key=lambda r: (float(str(r.get("time"))), int(str(r.get("id") or 0))))

    def who(r: dict) -> str:
        typ = str(r.get("type"))
        contract = str(r.get("contract") or str(r.get("text") or "").split(":")[0])
        if typ not in ("pnl", "fee", "fund"):
            return "skip"
        if contract and contract not in members:
            return "manual"
        if typ == "fund":
            return "strategy"
        _, sep, oid = str(r.get("text") or "").partition(":")
        text = texts.get(oid.strip()) if sep else None
        if text is None:
            return "unknown"
        if text.startswith("t-"):
            return "strategy"
        if text.startswith("ao-"):
            origin = stop_texts.get(text[3:])
            if origin is None:
                return "unknown"
            return "strategy" if origin.startswith("t-") else "manual"
        return "manual"

    by_hour: dict[datetime, Decimal] = defaultdict(Decimal)
    unknown: list[str] = []
    per_contract: dict[str, Decimal] = defaultdict(Decimal)
    strategy = Decimal(0)
    for r in book:
        t = when(r["time"])
        if t >= until:
            continue
        kind = who(r)
        chg = d(r.get("change"))
        if kind == "manual":
            by_hour[t.replace(minute=0, second=0, microsecond=0)] += chg
            per_contract[str(r.get("contract") or str(r.get("text")).split(":")[0])] += chg
        elif kind == "strategy":
            strategy += chg
        elif kind == "unknown":
            unknown.append(f"{kst(t)} {r.get('type')} {chg:+.4f} {str(r.get('text'))[:40]}")
    print("=== 사람 손익(계약별 합)")
    for k, v in sorted(per_contract.items(), key=lambda x: x[1]):
        print(f"  {k:14s} {v:+.3f}")
    print(f"=== 같은 구간 매매법 몫 {strategy:+.3f}")
    if unknown:
        print(f"=== 모름 {len(unknown)}줄(재분류 안 함 · 매매법 몫으로 둔다)")
        for line in unknown[:30]:
            print("  " + line)

    def balance_before(t: datetime) -> Decimal:
        last = None
        for r in book:
            if when(r["time"]) < t:
                last = d(r.get("balance"))
            else:
                break
        return last if last is not None else old_eq

    factor = Decimal(1)
    total = Decimal(0)
    lines: list[tuple[datetime, Decimal, Decimal]] = []
    print("=== 시간마다(틱이 흡수한 사람 손익)")
    for hour in sorted(by_hour):
        loss = by_hour[hour]
        if loss == 0:
            continue
        after = balance_before(hour + timedelta(hours=1))
        factor *= Decimal(1) - loss / after
        total += loss
        lines.append((hour, loss, after))
        print(f"  {kst(hour)} KST 시간 · 사람 {loss:+.3f} · 그 틱 잔고 {after:.2f}")

    acc = await c.get_account()
    total_now = d(acc.get("total"))
    print(
        f"거래소 총액 지금 {total_now:.2f} · 펀드 equity {old_eq:.2f} · 차이 {total_now - old_eq:+.2f}"
    )
    if abs(total_now - old_eq) > Decimal("0.5"):
        print("⚠️ 아직 흡수 안 된 변동이 있다 — 다음 정시 틱 뒤에 다시 돌린다(교정 뒤 값이 바뀐다)")

    new_twr = old_twr * factor
    new_peak = max(old_peak, new_twr)
    new_dd = (Decimal(1) - new_twr / new_peak) if new_peak > 0 else Decimal(0)
    new_contrib = old_contrib + total
    print("=== 교정")
    print(f"배수 f {factor:.6f} · 사람 손익 합 {total:+.3f}")
    print(f"twr {old_twr:.6f} → {new_twr:.6f} · 고점 {old_peak:.6f} → {new_peak:.6f}")
    print(
        f"낙폭 {old_dd:.2f}% → {new_dd * 100:.2f}% · deepest {old_deep * 100:.2f}% → {new_dd * 100:.2f}%"
    )
    print(f"contributed {old_contrib:.2f} → {new_contrib:.2f} · equity 그대로 {old_eq:.2f}")
    print(f"money_gain {old_eq - old_contrib:+.2f} → {old_eq - new_contrib:+.2f}(매매법 몫)")
    print("core_twr 는 안 건드린다 · anchor 도 그대로")
    if not APPLY:
        print("--- 계산만 했다. 적용은 머리말 2번(표식 → 다시 실행 → 3번 재기동 · 배포).")
        return
    backup = path.with_suffix(".json.bak_1015")
    shutil.copy2(path, backup)
    flows = list(flows0)
    for hour, loss, after in lines:
        flows.append(
            {
                "at": hour.isoformat(),
                "amount": str(loss.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)),
                "note": f"{NOTE} · 그 틱 잔고 {after:.2f} · deepest 는 교정 뒤 지금 값으로 다시 잡음",
            }
        )
    twr["twr"] = str(new_twr)
    twr["twr_peak"] = str(new_peak)
    twr["deepest"] = str(new_dd)
    twr["contributed"] = str(new_contrib)
    twr["flows"] = flows
    tmp = path.with_suffix(".json.tmp_1015")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    MARK.unlink(missing_ok=True)
    print(f"🔴 적용 완료 · 백업 {backup.name} · 이제 다음 정시 틱 전에 재기동 · 배포")


asyncio.run(main())
