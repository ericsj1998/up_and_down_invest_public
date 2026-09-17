# ruff: noqa: E501, RUF001
"""실계좌 구간(2026-09-01~) 에 룰 0.3 + P3 였다면 — 1H 캔들 위 상자 + 손익·MDD·청산 (의도적으로 남김).

세션 파일 `ab_parity_LIVE_{종목}_1h_1h_floor_pen075_w14.json`(2026-07-20~09-17 · 앞 6주는 워밍업).
P3 는 7-20 부터 걸어 9-01 의 보유 상태를 맞추고, 9-01~끝 은 **일별 평가(마크)** 로 잰다 — 8월에 잡아
9월까지 들고 있는 자리의 손익이 9월 성과다. 실계좌 2.1.1 의 실제 거래(감사 표 · UTC 가정)는 검정 마름모.

    set -a; . ./.env.dev; set +a; uv run python scripts/dev/_plot_live.py
    → logs/t279/trades/LIVE_all.png · logs/t279/ab_live_whatif.md
"""

from __future__ import annotations

import math
import sys
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "dev"))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts" / "research" / "scenarios"))
import _plot_trades as pt  # noqa: E402
import bbcci_lab as lab  # noqa: E402
import t279_lev_ladder as ll  # noqa: E402
import t279_portfolio as pf  # noqa: E402
import t279_sizing_combo as sc  # noqa: E402

LOG = ROOT / "logs" / "t279"
START = datetime(2026, 9, 1, tzinfo=UTC)
CAPITAL = 300.0
CAP = 3
PLANS = {"T1 P3(3x)": 3.0, "T2 P7(2.5x)": 2.5}


def _t(s: str) -> datetime:
    return datetime.fromisoformat(f"2026-{s}:00+00:00")


# 실계좌 2.1.1 거래 — docs/measurements/live_fund_audit_2026-09-18.md §1 (시각은 서버 DB 표기 · UTC 가정)
LIVE = [
    ("ETH_USDT", "09-05T15:50", "09-09T07:50", 2.44),
    ("NEAR_USDT", "09-06T11:55", "09-13T07:50", -3.42),
    ("BTC_USDT", "09-06T14:33", "09-08T13:41", -2.07),
    ("DOGE_USDT", "09-08T08:02", "09-09T15:07", 0.06),
    ("ETH_USDT", "09-09T09:39", "09-09T16:00", -0.51),
    ("BTC_USDT", "09-09T09:47", "09-09T22:08", -1.48),
    ("ETH_USDT", "09-09T20:30", "09-15T14:51", -2.55),
    ("BTC_USDT", "09-10T01:34", "09-10T12:47", -1.68),
    ("BTC_USDT", "09-10T16:09", "09-15T14:50", -1.73),
    ("BTC_USDT", "09-15T16:14", "09-17T03:00", 0.0),
]


def close_at(bars: list, tss: list[float], t: datetime) -> float:
    i = bisect_right(tss, t.timestamp()) - 1
    return bars[max(0, i)].c


def marks(
    taken: list[dict], bars_of: dict, lever: float, days: list[datetime]
) -> tuple[list[float], int]:
    """일별 평가 자본(시작 1.0) — 닫힌 매매는 청산 시각에 복리 반영, 열린 매매는 그날 마지막 종가로 평가."""
    tss_of = {s: [b.ts.timestamp() for b in bs] for s, bs in bars_of.items()}
    realized = 1.0
    liqs = 0
    out: list[float] = []
    settled: set[int] = set()
    for d in days:
        for idx, t in enumerate(taken):
            if idx in settled or t["_out"] > d:
                continue
            eff = lever * t["tilt"]
            liq = t["mae_pct"] >= ll.liq_dist(eff) or t["net_pct"] * eff <= -100.0
            pnl = -100.0 / CAP if liq else eff * t["net_pct"] / CAP
            liqs += int(liq and t["_out"] >= START)
            realized *= max(0.0, 1 + pnl / 100)
            settled.add(idx)
        unreal = 0.0
        for idx, t in enumerate(taken):
            if idx in settled or t["_in"] > d:
                continue
            eff = lever * t["tilt"]
            px = close_at(bars_of[t["symbol"]], tss_of[t["symbol"]], d)
            unreal += eff * (px / t["entry"] - 1) / CAP
        out.append(realized * (1 + unreal))
    return out, liqs


def main() -> int:
    pt.korean_font()
    rows3, _ = pf.load_window("LIVE", "floor_pen075_w14")
    rows2, _ = pf.load_window("LIVE", "floor_pen075")
    ll.attach_mae(rows3)
    sc.attach_tilt(rows3)
    pt.mark_p3(rows3)
    keys3 = {(t["symbol"], t["_in"]) for t in rows3}
    removed = [t for t in rows2 if (t["symbol"], t["_in"]) not in keys3]
    symbols = sorted({t["symbol"] for t in rows3} | {s for s, *_ in LIVE})
    bars_of = {s: lab.load_bars("GATE", s, "1h") for s in symbols}
    end = max(b.ts for bars in bars_of.values() for b in bars) + timedelta(hours=1)
    span = end - START

    fig, axes = plt.subplots(len(symbols), 1, figsize=(22, 4.6 * len(symbols)))
    for ax, sym in zip(axes, symbols, strict=True):
        bars = bars_of[sym]
        tss = [b.ts.timestamp() for b in bars]
        mid, up, lo = lab.bollinger([b.c for b in bars], 20, 2.0)
        live = [
            {"t_in": _t(a), "t_out": _t(b), "pct": p, "px": close_at(bars, tss, _t(a))}
            for s, a, b, p in LIVE
            if s == sym
        ]
        pt.draw(
            ax,
            bars,
            mid,
            up,
            lo,
            [t for t in rows3 if t["symbol"] == sym],
            [t for t in removed if t["symbol"] == sym],
            START,
            f"GATE {sym} · 실계좌 구간",
            span=span,
            live=live,
        )
    fig.suptitle(
        "실계좌 구간(2026-09-01 ~ 09-17 · Gate 선물 7종) 에 룰 0.3 + P3 였다면 — 실선 상자 = 룰 0.3 + P3 매매"
        "(8월에 잡아 9월까지 든 것 포함) · 회색 점선 = 건너뜀 · 보라 점선 = 룰 0.2 만"
        " · 검정 마름모/일점쇄선 = 실계좌 2.1.1 실제 거래(UTC 가정)",
        fontsize=10,
    )
    fig.tight_layout()
    out = LOG / "trades" / "LIVE_all.png"
    fig.savefig(out, dpi=115)
    plt.close(fig)

    lines: list[str] = []
    for label, keep in (
        ("Gate 7종(실계좌 바스켓 · NEAR 포함)", set(symbols)),
        ("Gate 6종(검증 표본 · NEAR 제외)", set(symbols) - {"NEAR_USDT"}),
    ):
        rows_k = [dict(t) for t in rows3 if t["symbol"] in keep]
        pt.mark_p3(rows_k)
        lines += score(rows_k, [t for t in removed if t["symbol"] in keep], bars_of, end, label)
        lines.append("")
    text = "\n".join(lines)
    print(text)
    (LOG / "ab_live_whatif.md").write_text(text + "\n", encoding="utf-8")
    _ = math
    print("saved", out)
    return 0


def score(
    rows3: list[dict], removed: list[dict], bars_of: dict, end: datetime, label: str
) -> list[str]:
    taken_all = [t for t in rows3 if t.get("taken")]
    carried = [t for t in taken_all if t["_in"] < START < t["_out"]]
    sept = [t for t in rows3 if t["_in"] >= START]
    lines = [
        f"### 실계좌 구간(2026-09-01~09-17) 룰 0.3 + P3 what-if — 세션 엔진 재현 · {label} · 300 USDT",
        "",
        "**9-01 에 들고 있던 자리(8월 진입)**",
        "",
        "| 종목 | 진입(UTC) | 청산(UTC) | 손절폭 | 순손익(1x) | 기울기 | 청산 사유 |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in carried:
        lines.append(
            f"| {t['symbol']} | {t['_in']:%m-%d %H:%M} | {t['_out']:%m-%d %H:%M} | {t['stop_pct']:.2f}% |"
            f" {t['net_pct']:+.2f}% | x{t['tilt']:.1f} | {pt.REASON.get(t['exit_reason'], t['exit_reason'])} |"
        )
    lines += [
        "",
        "**9월 신호**",
        "",
        "| 종목 | 진입(UTC) | 청산(UTC) | 손절폭 | 순손익(1x) | 기울기 | P3 | 청산 사유 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in sept:
        p3 = "잡음" if t.get("taken") else f"건너뜀({t.get('skip', '')})"
        lines.append(
            f"| {t['symbol']} | {t['_in']:%m-%d %H:%M} | {t['_out']:%m-%d %H:%M} | {t['stop_pct']:.2f}% |"
            f" {t['net_pct']:+.2f}% | x{t['tilt']:.1f} | {p3} | {pt.REASON.get(t['exit_reason'], t['exit_reason'])} |"
        )
    days = [START - timedelta(days=1)]
    while days[-1] + timedelta(days=1) <= end:
        days.append(days[-1] + timedelta(days=1))
    days[-1] = end
    lines += [
        "",
        "| 판 | 9월 손익 % | 9월 손익 USDT | 9월 MDD | 최악 하루 | 청산 | 잃은 날 / 날 | 9-17 보유 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, lever in PLANS.items():
        eq, liqs = marks(taken_all, bars_of, lever, days)
        base = eq[0]
        path = [v / base for v in eq]
        peak, mdd = path[0], 0.0
        for v in path:
            peak = max(peak, v)
            mdd = max(mdd, 1 - v / peak)
        rets = [path[i] / path[i - 1] - 1 for i in range(1, len(path))]
        neg = sum(1 for r in rets if r < -1e-9)
        held = [t["symbol"] for t in taken_all if t["exit_reason"] == "end"]
        lines.append(
            f"| {name} | {100 * (path[-1] - 1):+.2f}% | {CAPITAL * (path[-1] - 1):+.1f} | {100 * mdd:.1f}% |"
            f" {100 * min(rets):+.2f}% | {liqs} | {neg} / {len(rets)} | {', '.join(held) or '—'} |"
        )
    lines.append("| 실계좌 2.1.1(실제 · 09-05~09-15) | −2.7% | −8.0 | — | — | 0 | — | BTC |")
    rem = [t for t in removed if t["_in"] >= START]
    lines += [
        "",
        "룰 0.2 만 들어간 9월 자리(룰 0.3 이 거른 것): "
        + (
            ", ".join(
                f"{t['symbol']} {t['_in']:%m-%d} {t['net_pct']:+.1f}%(손절폭 {t['stop_pct']:.1f}%)"
                for t in rem
            )
            or "없음"
        ),
        f"9월 룰 0.3 신호 {len(sept)}건 중 P3 가 잡은 것 {sum(1 for t in sept if t.get('taken'))}건 · 이월 보유 {len(carried)}건",
    ]
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
