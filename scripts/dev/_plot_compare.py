# ruff: noqa
"""매매법 비교 PNG — (1) 룰 0.2 추종(세션 기록) · (2) 영상 투 캔들(원문) · (3) 사용자 수정판(밴드워크 금지 · 손절 1.0 ATR · 상단 밴드 익절).

사용법: uv run --with matplotlib python scripts/dev/_plot_compare.py KRW-BTC 2026-08-15 2026-09-14
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path("/home/ericsj1998/projects/up_and_down_invest")
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts" / "research" / "scenarios"))
import bbcci_lab as lab  # noqa: E402
import t279_reversal_spec as sp  # noqa: E402
import t279_reversal_user_fix as uf  # noqa: E402
import t279_bandwalk_follow as bw  # noqa: E402

SYM = sys.argv[1] if len(sys.argv) > 1 else "KRW-BTC"
T0 = datetime.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else "2026-08-15").replace(tzinfo=UTC)
T1 = datetime.fromisoformat(sys.argv[3] if len(sys.argv) > 3 else "2026-09-14").replace(tzinfo=UTC)
OUT = ROOT / "logs" / "t279" / "png"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams["axes.unicode_minus"] = False
COST = 0.157

market = "UPBIT" if SYM.startswith("KRW-") else "GATE"
bars = lab.load_bars(market, SYM, "1h")
s = lab.Series(SYM, bars, 60, {})
b = s.b
idx = [i for i, x in enumerate(b) if T0 <= x.ts < T1]
i0, i1 = idx[0], idx[-1]
xs = [b[i].ts for i in range(i0, i1 + 1)]


def draw_base(ax, title: str) -> None:
    for i in range(i0, i1 + 1):
        x = b[i]
        col = "#d33" if x.c >= x.o else "#27c"
        ax.plot([x.ts, x.ts], [x.lo, x.h], color=col, linewidth=0.6)
        ax.plot([x.ts, x.ts], [x.o, x.c], color=col, linewidth=2.2)
    ax.plot(
        xs,
        [s.up[i] for i in range(i0, i1 + 1)],
        color="#888",
        linewidth=0.8,
        label="BB(20,2) upper",
    )
    ax.plot(xs, [s.mid[i] for i in range(i0, i1 + 1)], color="#c80", linewidth=0.9, label="SMA20")
    ax.plot(
        xs,
        [s.lo[i] for i in range(i0, i1 + 1)],
        color="#888",
        linewidth=0.8,
        label="BB(20,2) lower",
    )
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.2)
    ax.legend(loc="upper left", fontsize=8)


def mark(ax, ti, e, to, x, g, stop, target=None, label=""):
    col = "#0a0" if g > 0 else "#a00"
    ax.scatter([ti], [e], marker="^", s=140, color="#06c", zorder=5)
    ax.scatter([to], [x], marker="v", s=140, color=col, zorder=5)
    ax.plot([ti, to], [e, x], color=col, linewidth=1.4, alpha=0.8)
    ax.hlines(stop, ti, to, colors="#a00", linestyles=":", linewidth=1)
    if target is not None:
        ax.hlines(target, ti, to, colors="#06c", linestyles="--", linewidth=0.8)
    ax.annotate(f"{g:+.2f}%{label}", (to, x), textcoords="offset points", xytext=(4, 6), fontsize=8)


tag = f"{SYM}_{T0:%Y%m%d}_{T1:%Y%m%d}"

# ── (1) 룰 0.2 추종
fig, ax = plt.subplots(figsize=(18, 8))
draw_base(
    ax,
    f"{SYM} 1H | (1) Trend rule 0.2: close > upper + 0.75 ATR, volume >= 2x, 4H not falling | stop = bar low - 0.2 ATR, exit = close < SMA20",
)
n_tr = 0
for path in sorted((ROOT / "logs" / "t279").glob(f"ab_parity_*_{SYM}_1h_1h_floor_pen075.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    for r in d["session"]["records"]:
        if not r["opened_at"] or r["gain_pct"] is None:
            continue
        ti = datetime.fromisoformat(r["opened_at"]).astimezone(UTC)
        to = datetime.fromisoformat(r["closed_at"]).astimezone(UTC)
        if not (T0 <= ti < T1):
            continue
        n_tr += 1
        mark(
            ax,
            ti,
            float(r["entry"]),
            to,
            float(r["exit"]),
            float(r["gain_pct"]),
            float(r.get("stop0") or r["stop"]),
        )
    break
ax.text(
    0.01,
    0.02,
    f"trades in range: {n_tr}  (^ entry, v exit: green profit / red loss, dotted = stop)  cost included",
    transform=ax.transAxes,
    fontsize=9,
)
fig.autofmt_xdate()
fig.savefig(OUT / f"cmp_trend02_{tag}.png", dpi=110, bbox_inches="tight")
plt.close(fig)

# ── (2) 영상 투 캔들 원문 · (3) 사용자 수정판
for variant in ("video", "userfix"):
    fig, ax = plt.subplots(figsize=(18, 8))
    if variant == "video":
        title = f"{SYM} 1H | (2) Video rule: two-candle reversal (J. Bollinger, as stated) | bar1 closes below lower band, bar2 big bullish bar closes back inside -> long, stop 0.1 ATR below lows, target = SMA20 at entry"
    else:
        title = f"{SYM} 1H | (3) User fix: two-candle + NO band-walk (<2 closes below band in last 6, SMA20 slope > -0.5 ATR/10) | stop 1.0 ATR below lows, take profit at upper band touch"
    draw_base(ax, title)
    n_ev = 0
    for i in range(max(i0, 200), i1 + 1):
        a = s.atr[i - 1]
        mid_i = s.mid[i]
        if not a or mid_i is None:
            continue
        evs = [(sg, e, lo) for sg, e, lo in uf.events_for(s, i, a) if sg.startswith("S1")]
        if not evs:
            continue
        _, entry, base_low = evs[0]
        if variant == "userfix":
            if uf.band_walk(s, i, a):
                continue
            stop = base_low - 1.0 * a
            g_raw, reason, k_exit, px = uf.simulate(
                s, i, entry, stop, "EU 상단 밴드 터치(이동)", mid_i
            )
            target = None
        else:
            stop = base_low - 0.1 * a
            g_raw, reason, k_exit, px = uf.simulate(
                s, i, entry, stop, "E1 고정 중심선(대조)", mid_i
            )
            target = mid_i
        n_ev += 1
        mark(ax, b[i].ts, entry, b[k_exit].ts, px, g_raw - COST, stop, target, f" ({reason})")
    ax.text(
        0.01,
        0.02,
        f"events in range: {n_ev}  (blue ^ entry at bar2 close, v exit, dotted = stop, dashed = fixed target)  cost {COST}%",
        transform=ax.transAxes,
        fontsize=9,
    )
    fig.autofmt_xdate()
    name = "cmp_video_twocandle" if variant == "video" else "cmp_userfix_twocandle"
    fig.savefig(OUT / f"{name}_{tag}.png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(variant, "events", n_ev)
print("trend trades", n_tr, "bars", len(xs))

# ── (4) 사용자 두 국면: 밴드워크 추종(상단 밖 마감 연속 2봉 → 롱 · 손절 첫 봉 저가 − 1.0 ATR · 종가 < SMA20 청산)
fig, ax = plt.subplots(figsize=(18, 8))
draw_base(
    ax,
    f"{SYM} 1H | (4) User regime rule: band-walk follow | 2 consecutive closes above upper band -> long at 2nd close | stop = 1st walk bar low - 1.0 ATR, exit = close < SMA20",
)
n_w = 0
for i in range(max(i0, 200), i1 + 1):
    a = s.atr[i - 1]
    if not a or s.mid[i] is None:
        continue
    f = bw.walk_signal(s, i, 2, False)
    if f is None:
        continue
    stop = b[f].lo - 1.0 * a
    g_raw, reason, k_exit, px = bw.simulate(s, i, b[i].c, stop, "M SMA20 이탈", False)
    n_w += 1
    mark(ax, b[i].ts, b[i].c, b[k_exit].ts, px, g_raw - COST, stop, None, f" ({reason})")
ax.text(
    0.01,
    0.02,
    f"events in range: {n_w}  (blue ^ entry at 2nd walk bar close, v exit, dotted = stop)  cost {COST}%",
    transform=ax.transAxes,
    fontsize=9,
)
fig.autofmt_xdate()
fig.savefig(OUT / f"cmp_bandwalk_{tag}.png", dpi=110, bbox_inches="tight")
plt.close(fig)
print("bandwalk events", n_w)
