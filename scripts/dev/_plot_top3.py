"""톱 3 그림 — 83차 일별 로그수익(logs/t279/ab_top3_daily.json)
→ 누적 곡선 + 낙폭 + 지표 표 (의도적으로 남김 · `_plot_n` 류).

uv run python scripts/dev/_plot_top3.py  → logs/t279/top3_curves.png · top3_table.png
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "logs" / "t279"
STARTS = {"업비트": date(2022, 1, 1), "Gate": date(2025, 7, 7), "Binance": date(2022, 1, 1)}
COLORS = {"T1": "#0f7b6c", "T2": "#1f77b4", "T3": "#b8860b", "기준": "#888888"}


def korean_font() -> None:
    # WSL 에 한글 폰트가 없으면 Windows 폰트(맑은 고딕)를 경로로 직접 등록한다.
    for path in (
        "/mnt/c/Windows/Fonts/malgun.ttf",
        "/mnt/c/Windows/Fonts/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ):
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
    for name in ("NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "AppleGothic"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def stats(logs: list[float]) -> dict[str, float]:
    eq, peak, mdd, under = 1.0, 1.0, 0.0, 0
    unders = []
    for x in logs:
        eq *= math.exp(x)
        if eq >= peak:
            peak = eq
            if under:
                unders.append(under)
            under = 0
        else:
            under += 1
            mdd = max(mdd, 1 - eq / peak)
    n = len(logs)
    neg = sum(1 for x in logs if x < -1e-12)
    return {
        "total": eq - 1,
        "annual": eq ** (365 / n) - 1,
        "mdd": mdd,
        "neg_day": neg / n,
        "worst_day": math.exp(min(logs)) - 1,
        "under_max": max([*unders, under]),
    } | {"_m": 0.0}


def main() -> int:
    korean_font()
    data = json.loads((LOG / "ab_top3_daily.json").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(2, 3, figsize=(18, 9), gridspec_kw={"height_ratios": [3, 1.4]})
    for col, (title, plans) in enumerate(data.items()):
        key = next(k for k in STARTS if k in title)
        d0 = STARTS[key]
        ax, axd = axes[0][col], axes[1][col]
        for name, logs in plans.items():
            short = name.split(" ")[0]
            color = COLORS.get(short, "#444")
            xs = [d0 + timedelta(days=i) for i in range(len(logs))]
            eq, peak = [], 1.0
            cur = 1.0
            dd = []
            for x in logs:
                cur *= math.exp(x)
                eq.append(cur * 300)
                peak = max(peak, cur)
                dd.append((cur / peak - 1) * 100)
            st = stats(logs)
            ax.plot(
                xs,
                eq,
                color=color,
                lw=1.6 if short == "T1" else 1.1,
                label=(
                    f"{name} · 연 {100 * st['annual']:+.0f}% · MDD {100 * st['mdd']:.0f}%"
                    f" · 손실일 {100 * st['neg_day']:.0f}%"
                ),
            )
            axd.plot(xs, dd, color=color, lw=0.9)
        ax.set_yscale("log")
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("자본 (USDT · 300 시작 · 로그)")
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=7.5, loc="upper left")
        axd.set_ylabel("고점 대비 낙폭 %")
        axd.grid(alpha=0.3)
        axd.axhline(-40, color="#b4423a", lw=0.7, ls="--")
    fig.suptitle(
        "T279 톱 3 · 룰 0.3(손절폭 ≥ 1.4%) + 사이징 — 격리 청산 · §24 · 복리"
        " · 300 USDT (2026-09-18)",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(LOG / "top3_curves.png", dpi=130)

    # 표 그림
    rows_tbl = []
    for title, plans in data.items():
        key = next(k for k in STARTS if k in title)
        for name, logs in plans.items():
            st = stats(logs)
            n = len(logs)
            months: dict[str, float] = {}
            d0 = STARTS[key]
            for i, x in enumerate(logs):
                k = (d0 + timedelta(days=i)).strftime("%Y-%m")
                months[k] = months.get(k, 0.0) + x
            mv = sorted(math.exp(v) - 1 for v in months.values())
            horizons = []
            for yrs in (1, 2, 3, 4):
                if 365 * yrs <= n:
                    s = stats(logs[: 365 * yrs])
                    horizons.append(f"{100 * s['total']:+,.0f}% ({100 * s['mdd']:.0f}%)")
                else:
                    horizons.append("—")
            rows_tbl.append(
                [
                    key,
                    name,
                    f"{100 * st['neg_day']:.0f}%",
                    f"{100 * sum(math.exp(x) - 1 for x in logs) / n:+.2f}%",
                    f"{100 * st['worst_day']:+.1f}%",
                    "0",
                    f"{100 * sum(mv) / len(mv):+.1f}%",
                    f"{sum(1 for v in mv if v < 0)}/{len(mv)}",
                    f"{100 * mv[0]:+.1f}%",
                    *horizons,
                    f"{100 * st['mdd']:.0f}%",
                    f"{st['under_max']}일",
                ]
            )
    cols = [
        "표본",
        "판",
        "손실 난 날",
        "하루 평균",
        "최악 하루",
        "청산",
        "월 평균",
        "음수 달",
        "최악 달",
        "1년 (MDD)",
        "2년 (MDD)",
        "3년 (MDD)",
        "4년 (MDD)",
        "전체 MDD",
        "최장 수면",
    ]
    fig2, ax2 = plt.subplots(figsize=(22, 0.55 * len(rows_tbl) + 1.5))
    ax2.axis("off")
    tbl = ax2.table(cellText=rows_tbl, colLabels=cols, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8.5)
    tbl.scale(1, 1.4)
    for (r, _c), cell in tbl.get_celld().items():
        if r == 0:
            cell.set_facecolor("#e8eef5")
            cell.set_text_props(weight="bold")
        elif rows_tbl[r - 1][1].startswith("T1"):
            cell.set_facecolor("#e6f4f1")
    ax2.set_title(
        "T279 톱 3 최종 — 룰 0.3 + P3(T1 권장) · P7(T2 보수) · P3+STOP-10(T3)"
        " vs 룰 0.2 + P3(기준) · 300 USDT · 청산 전부 0",
        fontsize=12,
        pad=14,
    )
    fig2.tight_layout()
    fig2.savefig(LOG / "top3_table.png", dpi=140)
    print("saved", LOG / "top3_curves.png", LOG / "top3_table.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
