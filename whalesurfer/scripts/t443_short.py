# ruff: noqa: RUF001, RUF003
# pyright: basic
"""T443 부록 2 — 공시 직후 며칠(진단 · 판정 아님 · 사용자 2026-10-10 "3일 뒤에는 해당 주식이 올랐는지").

① 공시일 반응: 공시 전 거래일 종가 → 공시일 종가(EDGAR 접수일 · 장 마감 뒤 접수면 반응은 다음 날로 밀린다).
② 따라 산 사람: 공시 다음 거래일 시가 진입 → 1 · 2 · 3 · 5 · 10 거래일째 종가(1 = 진입한 날 종가).
산(신규 · 늘림) · 판(줄임 · 전량 매도) · 신규만 · 전량 매도만 — 평균 · 중앙 · 오른 비율 · SPY 초과 · 비용 없음.
사건 · 가격 · 생존 편향은 T443 과 같다.

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/t443_short.py
    → logs/t279/t443/short.md
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t443_backtest as bt
from t443_bydate import on_or_before

HORIZONS = (1, 2, 3, 5, 10)
GROUPS = (
    ("산(신규 · 늘림)", ("new", "added")),
    ("판(줄임 · 전량 매도)", ("reduced", "exited")),
    ("신규만", ("new",)),
    ("전량 매도만", ("exited",)),
)


def f(x: float, u: str = "%") -> str:
    return "—" if x != x else f"{x:+.2f}{u}"


def main() -> int:
    tick = bt.load_tickers()
    events, _t, _tops, _u = bt.load_events(tick)
    px = bt.Px({e.ticker for e in events} | {"SPY"})
    ev = [e for e in events if px.has(e.ticker)]
    n = len(ev)
    bt.log(f"사건 {n:,}")
    filed = np.fromiter((e.filed.toordinal() for e in ev), dtype=np.int64, count=n)
    kind = np.asarray([e.kind for e in ev], dtype=object)
    win = np.asarray([bt.window_of(e.filed) for e in ev], dtype=object)
    g_fil = np.full(n, -1, dtype=np.int64)
    g_in = np.full(n, -1, dtype=np.int64)
    ends = np.zeros(n, dtype=np.int64)
    starts = np.zeros(n, dtype=np.int64)
    by_sym: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(ev):
        by_sym[e.ticker].append(i)
    for sym, idx in by_sym.items():
        ix = np.asarray(idx, dtype=np.int64)
        g_fil[ix] = on_or_before(px, sym, filed[ix])
        g_in[ix] = px.entry(sym, filed[ix])
        a, m = px.off[sym]
        starts[ix], ends[ix] = a, a + m
    s_fil = on_or_before(px, "SPY", filed)
    s_in = px.entry("SPY", filed)

    def cc(g0: np.ndarray, g1: np.ndarray) -> np.ndarray:
        ok = (g0 >= 0) & (g1 >= 0) & (g1 > g0) & (g1 < len(px.days))
        r = np.full(len(g0), np.nan)
        r[ok] = (px.c[g1[ok]] / px.c[g0[ok]] - 1) * 100
        return r

    # ① 공시일 반응 — 전 거래일 종가 → 공시일 종가 (공시일이 거래일일 때만)
    is_trading_day = (g_fil >= 0) & (px.days[np.clip(g_fil, 0, len(px.days) - 1)] == filed)
    g_prev = np.where(is_trading_day & (g_fil - 1 >= starts), g_fil - 1, -1)
    react = cc(g_prev, np.where(is_trading_day, g_fil, -1))
    s_prev = np.where(s_fil >= 1, s_fil - 1, -1)
    s_react = cc(s_prev, s_fil)
    react_ex = react - s_react

    # ② 따라 산 사람 — 다음 거래일 시가 → h 거래일째 종가
    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for h in HORIZONS:
        g1 = g_in + (h - 1)
        g1[(g_in < 0) | (g1 >= ends)] = -1
        raw = np.full(n, np.nan)
        ok = g1 >= 0
        raw[ok] = (px.c[g1[ok]] / px.o[g_in[ok]] - 1) * 100
        s1 = s_in + (h - 1)
        s_raw = np.full(n, np.nan)
        sok = (s_in >= 0) & (s1 < len(px.days))
        s_raw[sok] = (px.c[s1[sok]] / px.o[s_in[sok]] - 1) * 100
        out[h] = (raw, raw - s_raw)

    lines = [
        "# T443 부록 2 — 공시 직후 며칠 (진단 · 판정 아님 · 비용 없음 · SPY 초과 = 같은 날짜 SPY 대비)",
        "",
        f"- 사건 {n:,}(가격 있는 것) · 104 보고자 · 2013-07 ~ 2026-08 접수 · 생존 편향은 T443 과 같다",
        "- ② 진입 = 공시 다음 거래일 **시가** · h = 그 날부터 센 거래일(1 = 진입한 날 종가)",
        "",
        "## ① 공시일 반응 (공시 전 거래일 종가 → 공시일 종가)",
        "",
        "| 묶음 | n | 평균 | 중앙 | 오른 비율 | SPY 초과 평균 |",
        "|---|---|---|---|---|---|",
    ]
    for name, kinds in GROUPS:
        m = np.isin(kind, kinds) & ~np.isnan(react) & ~np.isnan(react_ex)
        x = react[m]
        lines.append(
            f"| {name} | {int(m.sum()):,} | {f(float(x.mean()))} | {f(float(np.median(x)))} | {float((x > 0).mean()) * 100:.1f}% | {f(float(react_ex[m].mean()), '%p')} |"
        )
    lines += [
        "",
        "## ② 공시 보고 따라 샀다면 — h 거래일 뒤 (전 기간)",
        "",
        "| 묶음 | " + " | ".join(f"{h}일 평균 · 오른 비율 · SPY 초과" for h in HORIZONS) + " |",
        "|---|" + "---|" * len(HORIZONS),
    ]
    for name, kinds in GROUPS:
        cells = []
        for h in HORIZONS:
            raw, ex = out[h]
            m = np.isin(kind, kinds) & ~np.isnan(raw) & ~np.isnan(ex)
            cells.append(
                f"{f(float(raw[m].mean()))} · {float((raw[m] > 0).mean()) * 100:.1f}% · {f(float(ex[m].mean()), '%p')}"
            )
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "### 3일 뒤 — 산 − 판 차이 · 구간별(SPY 초과 %p · 오른 비율 %p)",
        "",
        "| 구간 | 산 SPY 초과 | 판 SPY 초과 | 차이 | 산 오른 비율 | 판 오른 비율 | 신규만 SPY 초과 |",
        "|---|---|---|---|---|---|---|",
    ]
    raw3, ex3 = out[3]
    rng = np.random.default_rng(4432)
    for wname, _a, _z in [("전 기간", None, None), *bt.WINDOWS]:
        mw = np.ones(n, dtype=bool) if wname == "전 기간" else (win == wname)
        ok = mw & ~np.isnan(ex3)
        b, s_, nw = (
            ok & np.isin(kind, ("new", "added")),
            ok & np.isin(kind, ("reduced", "exited")),
            ok & (kind == "new"),
        )
        lines.append(
            f"| {wname} | {f(float(ex3[b].mean()))} | {f(float(ex3[s_].mean()))} | **{f(float(ex3[b].mean() - ex3[s_].mean()), '%p')}** | "
            f"{float((raw3[b] > 0).mean()) * 100:.1f}% | {float((raw3[s_] > 0).mean()) * 100:.1f}% | {f(float(ex3[nw].mean()))} |"
        )
    # 3일 산 − 판 차이의 20일 블록 부트스트랩 80% CI(전 기간)
    ok = ~np.isnan(ex3) & np.isin(kind, ("new", "added", "reduced", "exited"))
    is_buy = np.isin(kind, ("new", "added"))[ok]
    x = ex3[ok]
    keys, inv = np.unique(filed[ok] // 20, return_inverse=True)
    sb = np.bincount(inv, weights=np.where(is_buy, x, 0.0))
    nb = np.bincount(inv, weights=is_buy.astype(float))
    ss = np.bincount(inv, weights=np.where(~is_buy, x, 0.0))
    ns = np.bincount(inv, weights=(~is_buy).astype(float))
    bs = np.empty(1000)
    for r in range(1000):
        p = rng.integers(0, len(keys), len(keys))
        bs[r] = sb[p].sum() / max(nb[p].sum(), 1) - ss[p].sum() / max(ns[p].sum(), 1)
    lines += [
        "",
        f"3일 산 − 판 차이 80% CI(전 기간 · 20일 블록): [{np.quantile(bs, 0.1):+.3f}, {np.quantile(bs, 0.9):+.3f}]%p",
        "",
    ]
    out_path = bt.OUT / "short.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("SHORT_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
