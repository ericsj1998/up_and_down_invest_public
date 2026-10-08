"""펀드 원장 낙폭 교정 — 수동 매매 손실을 성과가 아니라 출금(흐름)으로 재분류. **사람이 실행한다**.

사용자 2026-10-09 00:30 KST "배포는 하지 말고, 낙폭을 고쳐주라" · 00:50 "네가 스크립트를 만들어서,
내가 그 파일을 동작" — 자동 도구는 서버 쓰기를 막으므로(분류기) 1.38.0 DB 정리처럼 사람이 돌린다.

## 사용 (WSL 셸 · 저장소 루트 · 정시 틱 직후 · 4H 경계 00 · 04 · 08 · 12 · 16 · 20Z 는 피함)

  1. 계산만 (파일 안 건드림 · 숫자 확인):
       bash scripts/ops/remote.sh scripts/ops/fix_fund_drawdown_1009.py
  2. 적용 (백업 `<파일>.bak_1009` 남김) — 컨테이너 표식으로 켠다:
       bash scripts/ops/remote.sh -- 'docker exec updown_live-api-1 touch /tmp/APPLY_FUND_FIX'
       bash scripts/ops/remote.sh scripts/ops/fix_fund_drawdown_1009.py
  3. 바로 API 재기동 (메모리가 파일을 다시 읽는다 · 재기동 전에 정시 틱이 돌면 메모리가 덮어쓴다):
       bash scripts/ops/remote.sh scripts/ops/restart_api_b_wait.sh
  4. 확인:
       bash scripts/ops/remote.sh scripts/ops/probe_fund_file_1009.sh      (twr 블록)
       bash scripts/ops/remote.sh scripts/ops/probe_brake_state.sh         (낙폭 · 브레이크)
  되돌리기: 컨테이너 안 `/app/logs/funds/<id>.json.bak_1009` 를 원래 이름으로 복사하고 재기동.

## 산수

펀드 원장(`twr`)은 앵커 = 거래소 계좌 총액이라 수동 매매 손실 L 이 그 시간의 성과
(기간 수익률 E_after / E_before)로 들어갔다. L 을 흐름으로 봤다면 기간 수익률은
(E_after + L) / E_before 였어야 하므로, 교정 배수는
    f = prod_i (1 + L_i / E_after_i)   (i = 손실이 흡수된 시간 단위 · E_after = 흡수 뒤 계좌 총액)
    twr' = twr * f · contributed' = contributed - sum(L) · flows 에 재분류 출금 줄 추가
고점(`twr_peak`)은 손실 전에 찍힌 값이면 그대로(교정 뒤 값이 더 높으면 그 값). `deepest` 는 알 수
있는 교정 뒤 점의 최댓값으로 다시 잡는다(원 경로 전체는 저장돼 있지 않다 — 흐름 note 에 적는다).
브레이크 원장(`core_twr`)은 안 건드린다 — 수동 매매는 애초에 안 들어갔다(T387). anchor 도 그대로.

## 수동 손실의 출처

거래소 자금 원장(account_book)에서 BTC_USDT 의 pnl · fee 줄을 두 창에서 모은다:
  1) 2026-10-01 13:30 ~ 15:30Z (사용자 웹 BTC · 713.64 → 609.42)
  2) 2026-10-08 14:00 ~ 15:30Z (사용자 웹 BTC 7회전 · 591.4 → 482.44)
그 창에 시스템(`t-`) BTC 매매는 없었다(끝난 주문 text · probe_manual_trades_1009.py). 시간 단위
묶음의 E_after 는 그 시간 마지막 줄 뒤 `balance` 다(같은 초의 줄은 잔고가 작은 쪽이 뒤).
10-08 14시 묶음 뒤 잔고 487.96 = 15Z 틱의 펀드 equity 와 같았다. 키 · 계정 값은 찍지 않는다.
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

WINDOWS = (
    (datetime(2026, 10, 1, 13, 30, tzinfo=UTC), datetime(2026, 10, 1, 15, 30, tzinfo=UTC)),
    (datetime(2026, 10, 8, 14, 0, tzinfo=UTC), datetime(2026, 10, 8, 15, 30, tzinfo=UTC)),
)
SYMBOL = "BTC_USDT"
MARK = Path("/tmp/APPLY_FUND_FIX")
APPLY = MARK.exists()
NOTE = "수동 매매 손실 재분류(사용자 2026-10-09) — 성과가 아니라 출금으로"


def d(v: object) -> Decimal:
    return Decimal(str(v))


def when(ts: object) -> datetime:
    return datetime.fromtimestamp(float(str(ts)), UTC)


async def manual_losses(c: GateTradeClient) -> list[tuple[datetime, Decimal, Decimal]]:
    """(시간 단위 시작 시각, 그 시간의 수동 손실 합(음수), 그 시간 마지막 줄 뒤 잔고) 목록."""
    req = c._request  # pyright: ignore[reportPrivateUsage]
    out: list[tuple[datetime, Decimal, Decimal]] = []
    for lo, hi in WINDOWS:
        rows = await req(
            "GET",
            f"/futures/{SETTLE}/account_book",
            params={
                "limit": "1000",
                "from": str(int(lo.timestamp())),
                "to": str(int(hi.timestamp())),
            },
        )
        by_hour: dict[datetime, list[tuple[datetime, Decimal, Decimal]]] = defaultdict(list)
        for r in rows:
            typ = str(r.get("type"))
            text = str(r.get("text") or "")
            if typ not in ("pnl", "fee") or not text.startswith(SYMBOL):
                continue
            t = when(r["time"])
            hour = t.replace(minute=0, second=0, microsecond=0)
            by_hour[hour].append((t, d(r.get("change") or 0), d(r.get("balance") or 0)))
        for hour in sorted(by_hour):
            # 같은 초의 줄(pnl · fee)은 순서가 안 남는다 — 전부 음수 변동이라 잔고가 작은 쪽이 뒤다.
            items = sorted(by_hour[hour], key=lambda x: (x[0], -x[2]))
            loss = sum((chg for _, chg, _ in items), Decimal(0))
            after = items[-1][2]
            out.append((hour, loss, after))
            print(
                f"  {hour:%m-%d %H}:00Z 수동 {SYMBOL} 줄 {len(items)} · "
                f"손실 {loss:+.3f} · 그 뒤 잔고 {after:.2f}"
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
        print("이미 재분류 줄이 있다 — 두 번 적용하지 않는다")
        return
    old_twr, old_peak, old_deep = d(twr["twr"]), d(twr["twr_peak"]), d(twr["deepest"])
    old_eq, old_contrib = d(twr["equity"]), d(twr["contributed"])
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== {stamp} · {'🔴 적용' if APPLY else '계산만'} · {path.name}")
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
    print("=== 수동 손실(거래소 자금 원장)")
    losses = await manual_losses(c)
    acc = await c.get_account()
    total_now = d(acc.get("total") or 0)
    print(
        f"거래소 총액 지금 {total_now:.2f} · 펀드 equity {old_eq:.2f} · "
        f"차이 {total_now - old_eq:+.2f}(다음 틱이 흡수)"
    )
    if abs(total_now - old_eq) > Decimal("0.5"):
        print("⚠️ 아직 흡수 안 된 변동이 있다 — 다음 정시 틱 뒤에 다시 돌린다(교정 뒤 값이 바뀐다)")

    factor = Decimal(1)
    total_loss = Decimal(0)
    for _hour, loss, after in losses:
        factor *= Decimal(1) + (-loss) / after
        total_loss += loss
    new_twr = old_twr * factor
    new_peak = max(old_peak, new_twr)
    new_dd = (Decimal(1) - new_twr / new_peak) if new_peak > 0 else Decimal(0)
    new_deep = new_dd  # 원 경로가 저장돼 있지 않다 — 알 수 있는 교정 뒤 점의 최댓값 = 지금
    new_contrib = old_contrib + total_loss  # total_loss 는 음수
    print("=== 교정")
    print(f"배수 f {factor:.6f} · 수동 손실 합 {total_loss:+.3f}")
    print(f"twr {old_twr:.6f} → {new_twr:.6f} · 고점 {old_peak:.6f} → {new_peak:.6f}")
    print(
        f"낙폭 {old_dd:.2f}% → {new_dd * 100:.2f}% · "
        f"deepest {old_deep * 100:.2f}% → {new_deep * 100:.2f}%"
    )
    print(f"contributed {old_contrib:.2f} → {new_contrib:.2f} · equity 그대로 {old_eq:.2f}")
    print(
        f"money_gain {old_eq - old_contrib:+.2f} → {old_eq - new_contrib:+.2f} "
        f"(매매법 몫 · 실제 손에 쥔 변화는 그대로 {old_eq - old_contrib:+.2f})"
    )
    print("core_twr 는 안 건드린다 · anchor 도 그대로")

    if not APPLY:
        print("--- 계산만 했다. 적용은 머리말 2번(표식 → 다시 실행 → 3번 재기동).")
        return
    backup = path.with_suffix(".json.bak_1009")
    shutil.copy2(path, backup)
    flows = list(twr.get("flows") or [])
    for hour, loss, after in losses:
        flows.append(
            {
                "at": hour.isoformat(),
                "amount": str(loss.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)),
                "note": (
                    f"{NOTE} · {SYMBOL} 수동 · 흡수 뒤 잔고 {after:.2f} · "
                    "deepest 는 교정 뒤 지금 값으로 다시 잡음"
                ),
            }
        )
    twr["twr"] = str(new_twr)
    twr["twr_peak"] = str(new_peak)
    twr["deepest"] = str(new_deep)
    twr["contributed"] = str(new_contrib)
    twr["flows"] = flows
    tmp = path.with_suffix(".json.tmp_1009")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    MARK.unlink(missing_ok=True)
    print(f"🔴 적용 완료 · 백업 {backup.name} · 이제 바로 3번(API 재기동)")


asyncio.run(main())
