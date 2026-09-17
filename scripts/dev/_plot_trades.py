"""룰 0.3 + P3 매매를 1H 캔들 위에 상자로 — 차트 채점 화면 대용
(집 밖에서 볼 수 있게 · 의도적으로 남김).

세션 파일(`ab_parity_{창}_{종목}_1h_1h_floor_pen075_w14.json`)의 매매를 그대로 그린다.
- 실선 상자(초록/빨강) = 룰 0.3 매매 중 P3(동시 3 · 연속 손절 2 정지)가 실제로 잡은 것
  · 라벨 = 순손익 % x 기울기 배수 · 청산 사유
- 회색 점선 상자 = 룰 0.3 신호였지만 P3 가 건너뛴 것(동시 보유 3 초과 · 그날 정지)
- 보라 점선 상자 = 룰 0.2 에는 있었지만 룰 0.3(손절폭 ≥ 1.4%)이 거른 것
- 진입선(검정) · 손절선(빨강 점선) · BB(20,2) 회색 띠 · SMA20 주황

    set -a; . ./.env.dev; set +a; uv run python scripts/dev/_plot_trades.py
    → logs/t279/trades/{창}_{종목}.png
"""

from __future__ import annotations

import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts" / "research" / "scenarios"))
import bbcci_lab as lab  # noqa: E402
import t279_lev_ladder as ll  # noqa: E402
import t279_portfolio as pf  # noqa: E402
import t279_sizing_combo as sc  # noqa: E402

OUT = ROOT / "logs" / "t279" / "trades"
KST = ZoneInfo("Asia/Seoul")
SPAN = timedelta(days=20)
CAP, LEVER = 3, 3.0
REASON = {"stop": "손절", "trend_end": "SMA20 이탈", "end": "구간 끝"}


def korean_font() -> None:
    for path in (
        "/mnt/c/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ):
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
    for name in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def mark_p3(rows: list[dict]) -> None:
    """t279_sizing_combo.run 과 같은 순서로 걸어 P3 가 잡은 매매에 taken=True 를 단다."""
    open_: list[dict] = []
    consec: dict = defaultdict(int)
    halted: set = set()
    for t in rows:
        ts = t["_in"]
        still = []
        for p in sorted(open_, key=lambda p: p["_out"]):
            if p["_out"] <= ts:
                d = p["_out"].date()
                consec[d] = consec[d] + 1 if p["exit_reason"] == "stop" else 0
                if consec[d] >= 2:
                    halted.add(d)
            else:
                still.append(p)
        open_ = still
        if ts.date() in halted:
            t["taken"], t["skip"] = False, "그날 정지"
            continue
        if len(open_) >= CAP:
            t["taken"], t["skip"] = False, "동시 3 초과"
            continue
        t["taken"], t["skip"] = True, ""
        open_.append(t)


def spans_for(rows: list[dict]) -> list[tuple[str, datetime]]:
    taken = [t for t in rows if t.get("taken")]
    if not taken:
        return []
    out: list[tuple[str, datetime]] = []
    recent = taken[-1]["_out"] - SPAN + timedelta(days=3)
    out.append(("최근", recent))
    best_s, best_v, worst_s, worst_v = None, -1e9, None, 1e9
    for t in taken:
        s = t["_in"] - timedelta(days=2)
        v = sum(u["net_pct"] for u in taken if s <= u["_in"] < s + SPAN)
        if v > best_v:
            best_s, best_v = s, v
        if v < worst_v:
            worst_s, worst_v = s, v
    for name, s in (("가장 번 20일", best_s), ("가장 잃은 20일", worst_s)):
        if s is not None and all(abs((s - o).days) > 10 for _, o in out):
            out.append((name, s))
    return out


def draw(
    ax, bars, mid, up, lo, rows3, rows2, s: datetime, title: str, span: timedelta = SPAN, live=()
) -> None:
    e = s + span
    tss = [b.ts for b in bars]
    i0, i1 = bisect_left(tss, s), bisect_left(tss, e)
    seg = bars[i0:i1]
    if not seg:
        return
    xs = [mdates.date2num(b.ts.astimezone(KST)) for b in seg]
    w = 0.7 / 24
    for x, b in zip(xs, seg, strict=True):
        col = "#c0392b" if b.c >= b.o else "#2b6cb0"
        ax.vlines(x, b.lo, b.h, color=col, lw=0.5)
        ax.add_patch(
            Rectangle(
                (x - w / 2, min(b.o, b.c)), w, max(abs(b.c - b.o), b.c * 1e-5), color=col, lw=0
            )
        )
    ax.fill_between(
        xs,
        [lo[i] if lo[i] is not None else float("nan") for i in range(i0, i1)],
        [up[i] if up[i] is not None else float("nan") for i in range(i0, i1)],
        color="#999",
        alpha=0.10,
        lw=0,
    )
    ax.plot(xs, [up[i] for i in range(i0, i1)], color="#777", lw=0.6)
    ax.plot(xs, [lo[i] for i in range(i0, i1)], color="#777", lw=0.6)
    ax.plot(xs, [mid[i] for i in range(i0, i1)], color="#e67e22", lw=0.9)

    def box(t: dict, style: str) -> None:
        if not (t["_in"] < e and t["_out"] > s):
            return
        x0 = mdates.date2num(max(t["_in"], s).astimezone(KST))
        x1 = mdates.date2num(min(t["_out"], e).astimezone(KST))
        entry = t["entry"]
        stop0 = entry * (1 - t["stop_pct"] / 100)
        exit_px = entry * (1 + (t["net_pct"] + 0.16) / 100)
        # 상자 = 진입가~청산가만. 손절선은 따로 점선(손절선까지 칠하면 아래서 산 것처럼 보인다).
        y0, y1 = min(entry, exit_px), max(entry, exit_px)
        if style == "taken":
            col = "#1e8449" if t["net_pct"] > 0 else "#c0392b"
            ax.add_patch(
                Rectangle(
                    (x0, y0), x1 - x0, y1 - y0, facecolor=col, alpha=0.18, edgecolor=col, lw=1.2
                )
            )
            ax.hlines(entry, x0, x1, color="black", lw=1.0)
            ax.hlines(stop0, x0, x1, color="#c0392b", lw=0.9, ls="--")
            ax.plot([x0], [entry], marker="^", color="black", ms=8, mfc="yellow", zorder=5)
            ax.plot([x1], [exit_px], marker="s", color=col, ms=6, zorder=5)
            ax.text(x0, stop0, " 손절선", fontsize=6, color="#c0392b", va="top", clip_on=True)
            ax.text(
                x0,
                y1,
                f" {t['net_pct']:+.1f}% x{t.get('tilt', 1.0):.1f}"
                f" {REASON.get(t['exit_reason'], t['exit_reason'])}",
                fontsize=8,
                color=col,
                va="bottom",
                weight="bold",
                clip_on=True,
            )
        elif style == "skip":
            ax.add_patch(
                Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor="#555", lw=0.9, ls=":")
            )
            ax.text(
                x0,
                y0,
                f" 건너뜀({t['skip']}) {t['net_pct']:+.1f}%",
                fontsize=7,
                color="#555",
                va="top",
                clip_on=True,
            )
        else:
            ax.add_patch(
                Rectangle(
                    (x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor="#8e44ad", lw=0.9, ls=":"
                )
            )
            ax.text(
                x0,
                y0,
                f" 0.2만·손절폭 {t['stop_pct']:.1f}% {t['net_pct']:+.1f}%",
                fontsize=7,
                color="#8e44ad",
                va="top",
                clip_on=True,
            )

    for t in rows2:
        box(t, "removed")
    for t in rows3:
        box(t, "taken" if t.get("taken") else "skip")
    for lv in live:
        if not (s <= lv["t_in"] < e):
            continue
        xi = mdates.date2num(lv["t_in"].astimezone(KST))
        xo = mdates.date2num(min(lv["t_out"], e).astimezone(KST))
        ax.annotate(
            f"실계좌 2.1.1 {lv['pct']:+.2f}%",
            xy=(xi, ax.get_ylim()[0] if False else lv["px"]),
            fontsize=7,
            color="black",
            xytext=(0, -14),
            textcoords="offset points",
            clip_on=True,
            annotation_clip=True,
        )
        ax.plot([xi, xo], [lv["px"], lv["px"]], color="black", lw=1.4, ls="-.")
        ax.plot([xi], [lv["px"]], marker="D", color="black", ms=5, mfc="white")
    lows = [b.lo for b in seg]
    highs = [b.h for b in seg]
    ax.set_xlim(xs[0] - 0.3, xs[-1] + 1.5)
    ax.set_ylim(min(lows) * 0.985, max(highs) * 1.02)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=KST))
    ax.grid(alpha=0.25)
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _p: f"{v:,.0f}" if v >= 100 else f"{v:,.4f}")
    )
    n_t = sum(1 for t in rows3 if t.get("taken") and s <= t["_in"] < e)
    pnl = sum(t["net_pct"] for t in rows3 if t.get("taken") and s <= t["_in"] < e)
    ax.set_title(
        f"{title} · {s.astimezone(KST):%Y-%m-%d} ~ {e.astimezone(KST):%Y-%m-%d} (KST)"
        f" · P3 매매 {n_t}건 · 합 {pnl:+.1f}%(1x 기준)",
        fontsize=10,
    )


def main() -> int:
    korean_font()
    OUT.mkdir(parents=True, exist_ok=True)
    for w in ("OOS", "GATE"):
        rows3, _ = pf.load_window(w, "floor_pen075_w14")
        rows2, _ = pf.load_window(w, "floor_pen075")
        ll.attach_mae(rows3)
        sc.attach_tilt(rows3)
        mark_p3(rows3)
        keys3 = {(t["symbol"], t["_in"]) for t in rows3}
        removed = [t for t in rows2 if (t["symbol"], t["_in"]) not in keys3]
        for sym in sorted({t["symbol"] for t in rows3}):
            r3 = [t for t in rows3 if t["symbol"] == sym]
            r2 = [t for t in removed if t["symbol"] == sym]
            market = "UPBIT" if sym.startswith("KRW-") else "GATE"
            bars = lab.load_bars(market, sym, "1h")
            mid, up, lo = lab.bollinger([b.c for b in bars], 20, 2.0)
            spans = spans_for(r3)
            if not spans:
                continue
            fig, axes = plt.subplots(len(spans), 1, figsize=(22, 5.2 * len(spans)))
            axes = [axes] if len(spans) == 1 else list(axes)
            for ax, (name, s) in zip(axes, spans, strict=True):
                draw(ax, bars, mid, up, lo, r3, r2, s.astimezone(UTC), f"{w} {sym} · {name}")
            taken = [t for t in r3 if t.get("taken")]
            fig.suptitle(
                f"룰 0.3 + P3 · {w} {sym} · 1H · P3 매매 {len(taken)}건"
                f" / 룰 0.3 신호 {len(r3)}건 / 룰 0.2 만 {len(r2)}건"
                " — 실선 상자 = 잡은 매매의 진입가~청산가(초록 이익 · 빨강 손실)"
                " · ▲ = 진입(돌파봉 종가) · ■ = 청산 · 회색 점선 = 건너뜀"
                " · 보라 점선 = 룰 0.3 이 거름"
                " · 검정 = 진입가 · 빨강 점선 = 초기 손절 · 주황 = SMA20 · 회색 띠 = BB(20,2)",
                fontsize=9.5,
            )
            fig.tight_layout()
            path = OUT / f"{w}_{sym}.png"
            fig.savefig(path, dpi=115)
            plt.close(fig)
            print("saved", path, len(taken), len(r3), len(r2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
