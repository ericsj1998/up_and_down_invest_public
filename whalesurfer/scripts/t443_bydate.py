# ruff: noqa: E402, RUF001, RUF003
# pyright: basic
"""T443 부록 — 날짜별 방향성(진단 · 판정 아님 · 사용자 2026-10-09 "날짜별로도 방향성이 없나?").

T443 은 공시 뒤 세 지점(1 · 3 · 12개월)만 쟀다. 여기서는 **날짜를 따라 이어서** 본다.
① 구간 나누기(실제 날짜): 그들이 거래한 분기(직전 분기 말 → 분기 말) · 공시 대기(분기 말 → 접수일) ·
   공시 뒤(접수일 → +21 · +63 · +252 거래일). 산 종목(신규 · 늘림) · 판 종목(줄임 · 전량 매도)의 SPY 초과 평균 · 중앙.
② 공시일 기준 하루하루(거래일 −95 ~ +252 · −95 ≈ 직전 분기 말 기준 누적) — 산 · 판 · 차이 곡선.
③ 연도별(접수 연도): 그들이 거래한 분기의 차이 · 공시 뒤 분기(접수일 → 다음 접수일)의 차이.
모두 종가 → 종가 · 사건마다 같은 무게 · 비용 없음(방향만 본다). 사건 · 가격 · 생존 편향은 T443 과 같다.

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/t443_bydate.py
    → logs/t279/t443/bydate.md
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t443_backtest as bt

TOL = 7
BUY = ("new", "added")
SELL = ("reduced", "exited")
SEGS = (
    ("A 그들이 거래한 분기", "prev", "per"),
    ("B 공시 대기(분기 말 → 접수일)", "per", "fil"),
    ("C 공시 뒤 1개월", "fil", 21),
    ("D 공시 뒤 3개월", "fil", 63),
    ("E 공시 뒤 1년", "fil", 252),
)
CURVE = (-95, -63, -45, -30, -15, -5, 0, 5, 10, 21, 42, 63, 126, 189, 252)
ANCHOR = -95


def periods_map() -> tuple[dict[tuple[str, str], tuple[date, date]], int]:
    """(보고자, 접수일) → (직전 분기 말, 이번 분기 말). 같은 날 두 보고는 뒤의 것(충돌 수를 센다)."""
    out: dict[tuple[str, str], tuple[date, date]] = {}
    clash = 0
    for path in sorted((bt.CACHE / "13f").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        cik = str(d["cik"])
        reps = sorted(d["reports"], key=lambda r: (str(r.get("period") or ""), str(r["filed"])))
        for i, rep in enumerate(reps):
            if i == 0:
                continue
            per, prev = rep.get("period"), reps[i - 1].get("period")
            if not (per and prev):
                continue
            key = (cik, str(rep["filed"]))
            clash += key in out
            out[key] = (date.fromisoformat(str(prev)), date.fromisoformat(str(per)))
    return out, clash


def on_or_before(px: bt.Px, sym: str, ords: np.ndarray) -> np.ndarray:
    """그 날 또는 그 전 마지막 거래일의 전역 인덱스(TOL 일 넘게 떨어지면 −1)."""
    a, n = px.off[sym]
    k = np.searchsorted(px.days[a : a + n], ords, side="right") - 1
    g = a + np.clip(k, 0, n - 1)
    g[(k < 0) | (ords - px.days[g] > TOL) | (ords < 0)] = -1
    return g


def cret(px: bt.Px, g0: np.ndarray, g1: np.ndarray) -> np.ndarray:
    """종가 → 종가 %(무효는 NaN)."""
    ok = (g0 >= 0) & (g1 >= 0) & (g1 > g0) & (g1 < len(px.days))
    r = np.full(len(g0), np.nan)
    r[ok] = (px.c[g1[ok]] / px.c[g0[ok]] - 1) * 100
    return r


def spread_ci(
    filed: np.ndarray, x: np.ndarray, is_buy: np.ndarray, rng: np.random.Generator
) -> tuple[float, float]:
    """산 − 판 평균 차이의 20일 블록 부트스트랩 80% CI."""
    ok = ~np.isnan(x)
    filed, x, is_buy = filed[ok], x[ok], is_buy[ok]
    keys, inv = np.unique(filed // 20, return_inverse=True)
    if len(keys) < 3:
        return (float("nan"), float("nan"))
    sb = np.bincount(inv, weights=np.where(is_buy, x, 0.0), minlength=len(keys))
    nb = np.bincount(inv, weights=is_buy.astype(float), minlength=len(keys))
    ss = np.bincount(inv, weights=np.where(~is_buy, x, 0.0), minlength=len(keys))
    ns = np.bincount(inv, weights=(~is_buy).astype(float), minlength=len(keys))
    out = np.empty(1000)
    for r in range(1000):
        p = rng.integers(0, len(keys), len(keys))
        out[r] = sb[p].sum() / max(nb[p].sum(), 1) - ss[p].sum() / max(ns[p].sum(), 1)
    return float(np.quantile(out, 0.1)), float(np.quantile(out, 0.9))


def f(x: float, u: str = "%") -> str:
    return "—" if x != x else f"{x:+.2f}{u}"


def main() -> int:
    tick = bt.load_tickers()
    events, _turn, _tops, _un = bt.load_events(tick)
    pmap, clash = periods_map()
    tickers = {e.ticker for e in events} | {"SPY"}
    bt.log(f"사건 {len(events):,} · 일봉 읽는 중")
    px = bt.Px(tickers)
    ev = [
        e
        for e in events
        if e.kind in BUY + SELL and px.has(e.ticker) and (e.cik, e.filed.isoformat()) in pmap
    ]
    n = len(ev)
    bt.log(f"쓸 사건 {n:,} · (보고자 · 접수일) 충돌 {clash}")
    filed = np.fromiter((e.filed.toordinal() for e in ev), dtype=np.int64, count=n)
    per = np.fromiter(
        (pmap[(e.cik, e.filed.isoformat())][1].toordinal() for e in ev), dtype=np.int64, count=n
    )
    prev = np.fromiter(
        (pmap[(e.cik, e.filed.isoformat())][0].toordinal() for e in ev), dtype=np.int64, count=n
    )
    nxt = np.fromiter(
        ((d.toordinal() if (d := e.next_filed) else -1) for e in ev), dtype=np.int64, count=n
    )
    is_buy = np.fromiter((e.kind in BUY for e in ev), dtype=bool, count=n)
    year = np.fromiter((e.filed.year for e in ev), dtype=np.int64, count=n)
    win = np.asarray([bt.window_of(e.filed) for e in ev], dtype=object)

    # 종목 · SPY 의 날짜 인덱스
    g: dict[str, np.ndarray] = {
        k: np.full(n, -1, dtype=np.int64) for k in ("prev", "per", "fil", "nxt")
    }
    s: dict[str, np.ndarray] = {
        k: np.full(n, -1, dtype=np.int64) for k in ("prev", "per", "fil", "nxt")
    }
    by_sym: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(ev):
        by_sym[e.ticker].append(i)
    for sym, idx in by_sym.items():
        ix = np.asarray(idx, dtype=np.int64)
        for k, arr in (("prev", prev), ("per", per), ("fil", filed), ("nxt", nxt)):
            g[k][ix] = on_or_before(px, sym, arr[ix])
    for k, arr in (("prev", prev), ("per", per), ("fil", filed), ("nxt", nxt)):
        s[k] = on_or_before(px, "SPY", arr)

    def seg_excess(a: str, b: str | int) -> np.ndarray:
        if isinstance(b, int):
            # 접수일 종가 → +b 거래일 종가 · SPY 는 같은 끝 날짜
            a_off = np.array(
                [px.off[e.ticker][0] + px.off[e.ticker][1] for e in ev], dtype=np.int64
            )
            g1 = g[a] + b
            g1[(g[a] < 0) | (g1 >= a_off)] = -1
            end_ord = np.where(g1 >= 0, px.days[np.clip(g1, 0, len(px.days) - 1)], -1)
            s1 = on_or_before(px, "SPY", end_ord)
            return cret(px, g[a], g1) - cret(px, s[a], s1)
        return cret(px, g[a], g[b]) - cret(px, s[a], s[b])

    rng = np.random.default_rng(4431)
    lines = [
        "# T443 부록 — 날짜별 방향성 (진단 · 판정 아님 · 종가 → 종가 · SPY 초과 · 비용 없음)",
        "",
        f"- 사건 {n:,}(산 {int(is_buy.sum()):,} · 판 {int((~is_buy).sum()):,}) · 104 보고자 · 2013-07 ~ 2026-08 접수 · 같은 (보고자 · 접수일) 두 보고 충돌 {clash}",
        "- 산 = 신규 · 늘림 · 판 = 줄임 · 전량 매도 · '차이' = 산 − 판(%p) · CI = 접수일 20일 블록 부트스트랩 80%",
        "- 🔴 생존 편향(폐지 종목 없음)은 T443 과 같다 — 산 · 판 양쪽에 같이 걸리므로 **차이**는 덜 휘둘린다",
        "",
        "## ① 구간 나누기 — 언제 방향이 있었나 (전 기간)",
        "",
        "| 구간 | n 산 | 산 평균 | 산 중앙 | n 판 | 판 평균 | 판 중앙 | 차이(산 − 판) | 80% CI |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    seg_vals: dict[str, np.ndarray] = {}
    for name, a, b in SEGS:
        x = seg_excess(a, b)
        seg_vals[name] = x
        mb, ms = is_buy & ~np.isnan(x), ~is_buy & ~np.isnan(x)
        ci = spread_ci(filed, x, is_buy, rng)
        diff = float(x[mb].mean() - x[ms].mean())
        lines.append(
            f"| {name} | {int(mb.sum()):,} | {f(float(x[mb].mean()))} | {f(float(np.median(x[mb])))} | {int(ms.sum()):,} | "
            f"{f(float(x[ms].mean()))} | {f(float(np.median(x[ms])))} | **{f(diff, '%p')}** | [{ci[0]:+.2f}, {ci[1]:+.2f}] |"
        )
    lines += [
        "",
        "### 구간별(T443 세 구간) 차이(산 − 판 · %p)",
        "",
        "| 구간 | " + " | ".join(nm for nm, _a, _b in SEGS) + " |",
        "|---|" + "---|" * len(SEGS),
    ]
    for wname, _wa, _wz in bt.WINDOWS:
        row = []
        for name, _a, _b in SEGS:
            x = seg_vals[name]
            m = win == wname
            mb, ms = m & is_buy & ~np.isnan(x), m & ~is_buy & ~np.isnan(x)
            row.append(f(float(x[mb].mean() - x[ms].mean()), "") if mb.any() and ms.any() else "—")
        lines.append(f"| {wname} | " + " | ".join(row) + " |")

    # ② 공시일 기준 하루하루 — 기준점 −95 거래일(≈ 직전 분기 말) 누적 SPY 초과
    lines += [
        "",
        "## ② 공시일 기준 하루하루 — 직전 분기 말 무렵(−95 거래일)부터 누적 SPY 초과",
        "",
        "0 = 접수일 · 음수 = 공시 전(그들이 거래하던 분기 · 45일 대기) · 양수 = 공시 뒤(우리가 따라 살 수 있는 때)",
        "",
        "| 거래일 | 산 평균 | 판 평균 | 차이 | 산 중앙 | 판 중앙 |",
        "|---|---|---|---|---|---|",
    ]
    ends = np.array([px.off[e.ticker][0] + px.off[e.ticker][1] for e in ev], dtype=np.int64)
    starts = np.array([px.off[e.ticker][0] for e in ev], dtype=np.int64)
    g0 = g["fil"]
    ga = g0 + ANCHOR
    ok_anchor = (g0 >= 0) & (ga >= starts)
    a_ord = np.where(ok_anchor, px.days[np.clip(ga, 0, len(px.days) - 1)], -1)
    s_anchor = on_or_before(px, "SPY", a_ord)
    for k in CURVE:
        gk = g0 + k
        ok = ok_anchor & (gk < ends) & (gk >= starts)
        gk2 = np.where(ok, gk, -1)
        k_ord = np.where(ok, px.days[np.clip(gk, 0, len(px.days) - 1)], -1)
        sk = on_or_before(px, "SPY", k_ord)
        if k == ANCHOR:
            x = np.where(ok, 0.0, np.nan)
        else:
            x = cret(px, np.where(ok, ga, -1), gk2) - cret(px, s_anchor, sk)
        mb, ms = is_buy & ~np.isnan(x), ~is_buy & ~np.isnan(x)
        lines.append(
            f"| {k:+d} | {f(float(x[mb].mean()))} | {f(float(x[ms].mean()))} | **{f(float(x[mb].mean() - x[ms].mean()), '%p')}** | "
            f"{f(float(np.median(x[mb])))} | {f(float(np.median(x[ms])))} |"
        )

    # ③ 연도별
    xa = seg_vals["A 그들이 거래한 분기"]
    xq = cret(px, g["fil"], g["nxt"]) - cret(px, s["fil"], s["nxt"])
    lines += [
        "",
        "## ③ 연도별(접수 연도) — 산 − 판 차이(%p)",
        "",
        "| 연도 | n | 그들이 거래한 분기 | 공시 뒤 분기(접수일 → 다음 접수일) | 공시 뒤 산 평균 | 공시 뒤 판 평균 |",
        "|---|---|---|---|---|---|",
    ]
    pos = 0
    yrs = sorted(set(year.tolist()))
    for y in yrs:
        m = year == y
        da = xa[m & is_buy & ~np.isnan(xa)].mean() - xa[m & ~is_buy & ~np.isnan(xa)].mean()
        qb, qs = xq[m & is_buy & ~np.isnan(xq)], xq[m & ~is_buy & ~np.isnan(xq)]
        dq = float(qb.mean() - qs.mean()) if len(qb) and len(qs) else float("nan")
        pos += dq > 0
        lines.append(
            f"| {y} | {int(m.sum()):,} | {f(float(da), '')} | **{f(dq, '')}** | {f(float(qb.mean()) if len(qb) else float('nan'))} | {f(float(qs.mean()) if len(qs) else float('nan'))} |"
        )
    lines += ["", f"공시 뒤 분기 차이가 + 인 해: {pos}/{len(yrs)}", ""]
    out = bt.OUT / "bydate.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("BYDATE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
