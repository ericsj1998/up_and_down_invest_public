# ruff: noqa
"""17차 N(반전 B + 거래량 돌파 추종)의 진입·청산을 1H 차트에 — 라이브 밴드(15m 마다 부분 밴드) · BB(20,2)+BB(4,4) · 반전/추종 구분.

사용자 지적 ① 라이브 밴드로 판정했나 → 엔진은 `_partial_band`(마감 19봉 + 지금 가격) 로 15m 마다 판정한다. 이 그림은 그 라이브 밴드를 그대로 그린다.
② 중심선 반대편 진입 → 진입 시점 라이브 중심선 대비 위치를 센다. ③ 추세 돌파 추종 → 추종 다리를 ■ 로 함께 그린다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path("/home/ericsj1998/projects/up_and_down_invest")
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import bb_reactive_lab as rx  # noqa: E402
import bbcci_lab as lab  # noqa: E402

plt.rcParams["axes.unicode_minus"] = False
CFG = sys.argv[2] if len(sys.argv) > 2 else "mix_N_ema_wr_vol2_flip"
payload = json.load(open(ROOT / "logs/bbcci/reactive24_upbit.json", encoding="utf-8"))
run = next(r for r in payload["runs"] if r["config"]["name"] == CFG)
trades: list[dict[str, Any]] = run["trades"]
for t in trades:
    t["entry_dt"] = datetime.fromisoformat(t["entry_ts"])
    t["exit_dt"] = datetime.fromisoformat(t["exit_ts"])

lab.set_venue("upbit")
series = lab.load_all("1h")
SYM = sys.argv[1] if len(sys.argv) > 1 else "KRW-BTC"
s = series[SYM]
ind = rx.extra_indicators(s)
bars = s.b
closes = [b.c for b in bars]
ts = [b.ts for b in bars]
idx = {t_: k for k, t_ in enumerate(ts)}
DAYS = 12
slip = lab.Costs.of("doc").slip_side


def live_bands(i0: int, i1: int) -> dict[str, list[float]]:
    """15m 마다 라이브 BB(20,2)·BB(4,4) — x 는 그 15m 봉의 마감 시각(봉 i 의 오른쪽 끝 = i+0.5)."""
    out: dict[str, list[float]] = {k: [] for k in ("x", "mid", "up", "lo", "up4", "lo4")}
    for i in range(i0, i1):
        span = s.lower_span(i, "15m")
        if span is None:
            continue
        win = closes[i - 19 : i]
        sum19, sumsq19 = sum(win), sum(c * c for c in win)
        c3 = closes[i - 3 : i]
        for lb in s.lower["15m"][span[0] : span[1] + 1]:
            frac = ((lb.ts + timedelta(minutes=15)) - bars[i].ts).total_seconds() / 3600
            mid, up, lo = rx._partial_band(sum19, sumsq19, lb.c)
            up4, lo4 = rx._partial_band44(c3, lb.c)
            out["x"].append(i - 0.5 + frac)
            out["mid"].append(mid)
            out["up"].append(up)
            out["lo"].append(lo)
            out["up4"].append(up4)
            out["lo4"].append(lo4)
    return out


def xof(dt: datetime) -> float:
    h = dt.replace(minute=0, second=0, microsecond=0)
    k = idx.get(h)
    return -1 if k is None else k + dt.minute / 60 - 0.5


# ② 진입 시점 라이브 중심선 대비 위치 (반전 다리만 · 전 종목)
wrong = total = 0
for t in trades:
    if t["kind"] != "fade":
        continue
    lb_start = t["entry_dt"] - timedelta(minutes=15)
    h = lb_start.replace(minute=0, second=0, microsecond=0)
    s_t = series[t["symbol"]]
    k = {b.ts: j for j, b in enumerate(s_t.b)}.get(h) if t["symbol"] == SYM else None
    if k is None or k < 20:
        continue
    px = t["entry"] / (1 + t["direction"] * slip)
    win = [b.c for b in s_t.b[k - 19 : k]]
    mid, _u, _l = rx._partial_band(sum(win), sum(c * c for c in win), px)
    total += 1
    if (t["direction"] > 0 and px > mid) or (t["direction"] < 0 and px < mid):
        wrong += 1
print(f"{SYM} 반전 진입 {total}건 중 라이브 중심선 반대편(롱인데 위 · 숏인데 아래) {wrong}건")


def window_stats(i0: int, i1: int) -> tuple[int, float, float]:
    seg = bars[i0:i1]
    lo, hi = min(b.lo for b in seg), max(b.h for b in seg)
    rng = (hi - lo) / seg[0].c * 100
    net = abs(seg[-1].c - seg[0].c) / seg[0].c * 100
    n = sum(1 for t in trades if t["symbol"] == SYM and seg[0].ts <= t["entry_dt"] < seg[-1].ts)
    return n, rng, net


step = 24 * DAYS
cands: list[tuple[int, int, int, float, float]] = []
for i0 in range(300, len(bars) - step, 24 * 3):
    n, rng, net = window_stats(i0, i0 + step)
    if n >= 4:
        cands.append((i0, i0 + step, n, rng, net))
side = sorted([c for c in cands if c[4] / max(c[3], 1e-9) < 0.25], key=lambda c: (-c[2], c[3]))
trend = sorted([c for c in cands if c[4] / max(c[3], 1e-9) > 0.6], key=lambda c: -c[2])
picks = [("RANGE", side[0]), ("TREND", trend[0])]
print(SYM, "picks", [(k, str(bars[c[0]].ts.date()), c[2]) for k, c in picks])

fig, axes = plt.subplots(2, 1, figsize=(22, 8.5 * 2))
for ax, (kind, (i0, i1, n, rng, net)) in zip(axes, picks, strict=True):
    seg = bars[i0:i1]
    x = list(range(i0, i1))
    for xi, b in zip(x, seg, strict=True):
        col = "#d33" if b.c >= b.o else "#26a"
        ax.vlines(xi, b.lo, b.h, color=col, linewidth=0.8)
        ax.add_patch(
            plt.Rectangle((xi - 0.35, min(b.o, b.c)), 0.7, abs(b.c - b.o) or 1e-9, color=col)
        )
    lv = live_bands(i0, i1)
    ax.plot(lv["x"], lv["up"], color="#555", linewidth=0.9, label="live BB(20,2) (15m)")
    ax.plot(lv["x"], lv["lo"], color="#555", linewidth=0.9)
    ax.plot(lv["x"], lv["mid"], color="#555", linewidth=0.9, linestyle="--")
    ax.plot(
        lv["x"],
        lv["up4"],
        color="#1f4fd8",
        linewidth=0.8,
        linestyle="-.",
        label="live BB(4,4) (15m)",
    )
    ax.plot(lv["x"], lv["lo4"], color="#1f4fd8", linewidth=0.8, linestyle="-.")
    ax.plot(x, [ind["ema20"][i] for i in x], color="#e6a700", linewidth=1.1, label="EMA20")
    ax.plot(x, [ind["ema50"][i] for i in x], color="#0a8", linewidth=1.1, label="EMA50")
    ax.plot(x, [ind["ema200"][i] for i in x], color="#a0a", linewidth=1.1, label="EMA200")
    for t in trades:
        if t["symbol"] != SYM or not (seg[0].ts <= t["entry_dt"] < seg[-1].ts + timedelta(hours=1)):
            continue
        xe, xx = xof(t["entry_dt"]), min(xof(t["exit_dt"]), i1 - 1)
        exit_px = t["entry"] * (1 + t["direction"] * t["gross_pct"] / 100)
        win = t["net_pct"] > 0
        is_trend = t["kind"] == "trend"
        ax.plot(
            [xe, xx],
            [t["entry"], exit_px],
            color="#0a0" if win else "#c00",
            linewidth=1.6,
            alpha=0.85,
        )
        marker = "s" if is_trend else ("^" if t["direction"] > 0 else "v")
        ax.scatter(
            [xe],
            [t["entry"]],
            marker=marker,
            s=150 if is_trend else 140,
            color="#0a0" if t["direction"] > 0 else "#c00",
            edgecolor="k",
            zorder=5,
        )
        ax.scatter([xx], [exit_px], marker="x", s=90, color="k", zorder=5)
        tag = ("T" if is_trend else "R") + ("L" if t["direction"] > 0 else "S")
        ax.annotate(
            f"{tag} {t['net_pct']:+.2f}% {t['exit_reason']}",
            (xe, t["entry"]),
            textcoords="offset points",
            xytext=(0, -18 if t["direction"] > 0 else 12),
            fontsize=8,
            ha="center",
            color="#0a0" if win else "#c00",
        )
    ax.set_xlim(i0 - 1, i1)
    lo_, hi_ = min(b.lo for b in seg), max(b.h for b in seg)
    ax.set_ylim(lo_ * 0.995, hi_ * 1.005)
    ticks = list(range(i0, i1, 24))
    ax.set_xticks(ticks)
    ax.set_xticklabels([bars[k].ts.strftime("%m-%d") for k in ticks], fontsize=8)
    ax.set_title(
        f"{SYM} 1H · {kind} {seg[0].ts.date()} ~ {seg[-1].ts.date()} · rule N (reversal B + volume-breakout trend) · entries {n}  "
        "| bands = LIVE (19 closed + current price, every 15m) | ^v = reversal (R) · square = trend-follow (T) · x exit · green win / red loss",
        fontsize=10.5,
    )
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
out = ROOT / f"logs/bbcci/img/n_entries_live_{SYM}.png"
fig.tight_layout()
fig.savefig(out, dpi=140)
print("→", out)
