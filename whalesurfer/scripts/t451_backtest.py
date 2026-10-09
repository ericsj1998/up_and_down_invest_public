# ruff: noqa: RUF001
# pyright: basic
"""T451 백테스트 — 사전 등록 §2 · §3 그대로 (`docs/planning/tasks/T451_whalesurfer_fast_filings.md` · 2026-10-10).

진입 = 접수일 다음 거래일 시가 · 보유 5 · 21 · 63 · 126 거래일째 종가 · 롱 · 비용 왕복 0.20%(민감도 0.10 · 0.40 표시).
칸(규칙 x 보유) 조건 = 세 구간 모두 n ≥ 100 ∧ 순 > 0 ∧ SPY 초과 > 0 ∧ 순 > 국소 무작위 대조 90분위(같은 종목 ·
사건일 ±63거래일 안 무작위 진입 · 같은 보유 · 1,000회) ∧ 2022 ~ 26 SPY 초과 20일 블록 부트스트랩 80% CI 하한 > 0.
✅ 칸 = 조건 ∧ 이웃 보유 하나 이상도 조건. 규칙 ✅ = ✅ 칸이 하나 이상. F4-SELL 은 진단(판정 밖).

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/t451_backtest.py
    → logs/t279/t451/result.md · summary.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t443_backtest as bt

OUT = bt.ROOT / "logs" / "t279" / "t451"
RULES = ("F4-BUY", "F4-CLUSTER", "F4-CEOCFO", "F4-BIG", "F4-10PCT", "13D-NEW")
DIAG = "F4-SELL"
HOLDS = (5, 21, 63, 126)
COST = 0.20
LOCAL = 63
ROUNDS = 1000
MAX_EVENTS = 20_000
MIN_N = 100
WINDOWS = (
    ("2013 ~ 19", "2013-07-01", "2020-01-01"),
    ("2020 ~ 21", "2020-01-01", "2022-01-01"),
    ("2022 ~ 26", "2022-01-01", "2026-10-01"),
)
CI_WINDOW = "2022 ~ 26"


def f(x: float, u: str = "%") -> str:
    return "—" if x != x else f"{x:+.2f}{u}"


def win_of(d: str) -> str | None:
    for name, a, z in WINDOWS:
        if a <= d < z:
            return name
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    events = json.loads((bt.CACHE / "t451_events.json").read_text(encoding="utf-8"))
    events = [e for e in events if e["ticker"] and win_of(e["filed"])]
    px = bt.Px({e["ticker"] for e in events} | {"SPY"})
    rng = np.random.default_rng(451)
    bt.log(f"사건(티커 · 구간 안) {len(events):,} · 일봉 종목 {len(px.off):,}")

    def leg_of(rule: str) -> dict[str, np.ndarray]:
        ev = [e for e in events if e["rule"] == rule and px.has(e["ticker"])]
        n = len(ev)
        from datetime import date as _d

        filed = np.fromiter(
            (_d.fromisoformat(e["filed"]).toordinal() for e in ev), dtype=np.int64, count=n
        )
        g_in = np.full(n, -1, dtype=np.int64)
        start = np.zeros(n, dtype=np.int64)
        end = np.zeros(n, dtype=np.int64)
        by: dict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(ev):
            by[e["ticker"]].append(i)
        for sym, idx in by.items():
            ix = np.asarray(idx, dtype=np.int64)
            g_in[ix] = px.entry(sym, filed[ix])
            a, m = px.off[sym]
            start[ix], end[ix] = a, a + m
        s_in = px.entry("SPY", filed)
        win = np.asarray([win_of(e["filed"]) for e in ev], dtype=object)
        return {"filed": filed, "g_in": g_in, "start": start, "end": end, "s_in": s_in, "win": win}

    def returns(leg: dict[str, np.ndarray], h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        g_in, end = leg["g_in"], leg["end"]
        g_out = g_in + h - 1
        ok = (g_in >= 0) & (g_out < end)
        raw = np.full(len(g_in), np.nan)
        raw[ok] = (px.c[g_out[ok]] / px.o[g_in[ok]] - 1) * 100
        s_in = leg["s_in"]
        s_out = s_in + h - 1
        sok = (s_in >= 0) & (s_out < len(px.days))
        spy = np.full(len(g_in), np.nan)
        spy[sok] = (px.c[s_out[sok]] / px.o[s_in[sok]] - 1) * 100
        net = raw - COST
        return raw, net, net - spy

    def local_q90(leg: dict[str, np.ndarray], mask: np.ndarray, h: int) -> float:
        idx = np.flatnonzero(mask)
        if len(idx) > MAX_EVENTS:
            idx = rng.choice(idx, MAX_EVENTS, replace=False)
        g = leg["g_in"][idx]
        lo = np.maximum(leg["start"][idx], g - LOCAL)
        hi = np.minimum(leg["end"][idx] - h, g + LOCAL)
        ok = hi >= lo
        lo, hi = lo[ok], hi[ok]
        if len(lo) == 0:
            return float("nan")
        means = np.empty(ROUNDS)
        for r in range(ROUNDS):
            gr = lo + (rng.random(len(lo)) * (hi - lo + 1)).astype(np.int64)
            means[r] = float(np.mean((px.c[gr + h - 1] / px.o[gr] - 1) * 100 - COST))
        return float(np.quantile(means, 0.9))

    def block_ci(filed: np.ndarray, x: np.ndarray) -> tuple[float, float]:
        keys, inv = np.unique(filed // 20, return_inverse=True)
        if len(keys) < 3:
            return (float("nan"), float("nan"))
        sums = np.bincount(inv, weights=x)
        cnts = np.bincount(inv).astype(float)
        out = np.empty(1000)
        for r in range(1000):
            p = rng.integers(0, len(keys), len(keys))
            out[r] = sums[p].sum() / cnts[p].sum()
        return float(np.quantile(out, 0.1)), float(np.quantile(out, 0.9))

    lines = [
        "# T451 빠른 공시 백테스트 — 결과 (사전 등록 자 · 비용 왕복 0.20% · 진입 = 접수 다음 거래일 시가)",
        "",
        "- 🔴 생존 편향: 지금 상장표에 없는 회사(폐지 · 합병)는 빠졌다 — 롱을 부풀리는 쪽. 커버리지는 `logs/t279/t451/coverage` 표.",
        "- 조건 = n ≥ 100 ∧ 순 > 0 ∧ SPY 초과 > 0 ∧ 순 > 국소 무작위 90분위(±63거래일) ∧ (2022 ~ 26) SPY 초과 CI 하한 > 0",
        "",
    ]
    summary: dict[str, Any] = {"rules": {}}
    passed: list[str] = []
    buy_ex: dict[int, float] = {}
    for rule in (*RULES, DIAG):
        leg = leg_of(rule)
        cond: dict[int, bool] = {}
        rows: list[str] = []
        for h in HOLDS:
            raw, net, ex = returns(leg, h)
            ok_all = True
            for wname, _a, _z in WINDOWS:
                m = (leg["win"] == wname) & ~np.isnan(ex)
                n = int(m.sum())
                if n == 0:
                    rows.append(f"| {h} | {wname} | 0 | — | — | — | — | — | — | — | ✗ |")
                    ok_all = False
                    continue
                mn, mx = float(net[m].mean()), float(ex[m].mean())
                q90 = local_q90(leg, m, h) if rule != DIAG else float("nan")
                ci = (
                    block_ci(leg["filed"][m], ex[m])
                    if wname == CI_WINDOW
                    else (float("nan"), float("nan"))
                )
                ok = (
                    n >= MIN_N
                    and mn > 0
                    and mx > 0
                    and (rule == DIAG or mn > q90)
                    and (wname != CI_WINDOW or ci[0] > 0)
                )
                ok_all &= ok
                ci_s = f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if wname == CI_WINDOW else "—"
                rows.append(
                    f"| {h} | {wname} | {n:,} | {mn:+.2f}% | {float(np.median(net[m])):+.2f}% | {float((raw[m] > 0).mean()) * 100:.0f}% | "
                    f"{mx:+.2f}%p | {f(q90)} | {ci_s} | {f(mn + 0.10)} · {f(mn - 0.20)} | {'✓' if ok else '✗'} |"
                )
                summary["rules"].setdefault(rule, {}).setdefault(str(h), {})[wname] = {
                    "n": n,
                    "net": mn,
                    "ex": mx,
                    "q90": q90,
                    "ci": ci,
                    "ok": ok,
                }
            cond[h] = ok_all
            if rule == "F4-BUY":
                mm = ~np.isnan(ex)
                buy_ex[h] = float(ex[mm].mean())
        cells = []
        for i, h in enumerate(HOLDS):
            nb = [HOLDS[j] for j in (i - 1, i + 1) if 0 <= j < len(HOLDS)]
            cells.append((h, cond[h] and any(cond[x] for x in nb)))
        verdict = [h for h, ok in cells if ok]
        title = (
            "진단 · 판정 밖"
            if rule == DIAG
            else ("✅ " + " · ".join(f"{h}일" for h in verdict) if verdict else "⛔")
        )
        if verdict and rule != DIAG:
            passed.append(f"{rule}({' · '.join(str(h) for h in verdict)}일)")
        lines += [
            f"## {rule} — {title}",
            "",
            "칸 조건(세 구간 모두): "
            + " · ".join(f"{h}일 {'✓' if cond[h] else '✗'}" for h in HOLDS),
            "",
            "| 보유 | 구간 | n | 평균 순 | 중앙 순 | 오른 비율 | SPY 초과 | 국소 무작위 90분위 | SPY 초과 80% CI | 순 비용 0.10 · 0.40 | 조건 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
            *rows,
            "",
        ]
        if rule == DIAG:
            mm_lines = []
            for h in HOLDS:
                _r, _n, ex = returns(leg, h)
                sx = float(ex[~np.isnan(ex)].mean())
                mm_lines.append(f"{h}일 매수 − 매도 SPY 초과 차이 {buy_ex[h] - sx:+.2f}%p")
            lines += ["진단: " + " · ".join(mm_lines), ""]
        bt.log(f"{rule} 끝")
    lines += ["## 종합", "", " · ".join(passed) if passed else "통과 규칙 없음(⛔ 전부)", ""]
    summary["passed"] = passed
    (OUT / "result.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("\n".join(lines))
    print("T451_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
