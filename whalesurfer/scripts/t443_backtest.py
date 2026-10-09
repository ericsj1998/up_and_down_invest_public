# pyright: basic
"""T443 — WhaleSurfer 따라 사기 백테스트 (사전 등록 `docs/planning/tasks/T443_whalesurfer_backtest.md` · 2026-10-09).

재료
- 13F 역사 `cache/whalesurfer/13f/{cik}.json`(T442 crawl) · CUSIP → 티커 `cache/whalesurfer/cusip_figi.json`(OpenFIGI · exch US)
- 일봉 = DB `candles`(NASDAQ · NYSE · 1d · 토스 적재 · ts = 뉴욕 자정) · SPY = 대조 지수

사건(§1) = 보고자 m 의 연속 보고(q−1 → q) 사이 CUSIP 별 변화(현물 줄만): 신규 · 늘림 · 줄임 · 전량 매도.
주 수 변화 ±40% 밖인데 가치 변화 ±15% 안이면 "분할 의심" 으로 뺀다. 시각 = 접수일 · 진입 = 다음 거래일 시가.
규칙(§2) BUY-Q · BUY-1M/3M/12M · SELL-Q(숏) · SELL-FLAT(보고자가 줄이거나 팔 때까지 보유) · HOLD-12M(상위 10 비중).
대조 = ① SPY 같은 기간 ② 같은 종목 무작위 날짜 1,000회. 구간 셋(접수일 기준). 회전율 삼분위(최근 8분기 교체율).
판정(§3) = 세 구간 모두 순 > 0 ∧ SPY 초과 > 0 ∧ 무작위 90분위 위 · n ≥ 100 · 2022 ~ 26 20일 블록 부트스트랩 80% CI 하한 > 0.

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/t443_backtest.py
    → logs/t279/t443/result.md · summary.json · cache/whalesurfer/buyq_realized.json(화면 "그때 샀다면")
"""

from __future__ import annotations

import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date
from pathlib import Path
from typing import Any, cast

import numpy as np
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "whalesurfer"
OUT = ROOT / "logs" / "t279" / "t443"
COST = 0.10  # 왕복 % (미국 주식 테이커 가정 · 토스 실측 전)
WINDOWS = (
    ("2013 ~ 19", date(2013, 5, 1), date(2020, 1, 1)),
    ("2020 ~ 21", date(2020, 1, 1), date(2022, 1, 1)),
    ("2022 ~ 26", date(2022, 1, 1), date(2026, 9, 1)),
)
CI_WINDOW = "2022 ~ 26"
HOLDS = {"BUY-1M": 21, "BUY-3M": 63, "BUY-12M": 252, "HOLD-12M": 252}
TOP_HOLD = 10
SPLIT_SHARE = (0.6, 1.4)
SPLIT_VALUE = 0.15
MIN_N = 100
TOL = 7  # 접수일 → 진입 봉 · 목표일 → 청산 봉 사이 허용 달력일(자료 시작 전 · 끝 뒤는 무효)
RANDOM_ROUNDS = 1000
RANDOM_MAX_EVENTS = 20_000
GROUPS = ("전체", "저회전", "중회전", "고회전")
RULES: dict[str, tuple[str, ...]] = {
    "BUY-Q": ("new", "added"),
    "BUY-1M": ("new", "added"),
    "BUY-3M": ("new", "added"),
    "BUY-12M": ("new", "added"),
    "SELL-FLAT": ("new", "added"),
    "SELL-Q": ("reduced", "exited"),
}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


@dataclass(frozen=True, slots=True)
class Event:
    cik: str
    filed: date
    next_filed: date | None
    # SELL-FLAT — 같은 보고자가 이 CUSIP 을 줄이거나 판 첫 접수일(없으면 None = 아직 보유)
    flat_at: date | None
    cusip: str
    ticker: str
    kind: str


class Px:
    """전 종목 일봉을 한 배열로 — 종목별 (offset, n) · 날짜 서수(int64) · 시가 · 종가(float32)."""

    def __init__(self, tickers: set[str]) -> None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("DATABASE_URL 이 없다 — set -a; . ./.env.dev; set +a")
        engine = sa.create_engine(url.replace("+asyncpg", "+psycopg"))
        syms = sorted(tickers)
        days: list[int] = []
        opens: list[float] = []
        closes: list[float] = []
        self.off: dict[str, tuple[int, int]] = {}
        with engine.connect().execution_options(stream_results=True) as conn:
            for i in range(0, len(syms), 300):
                rows = conn.execute(
                    sa.text(
                        """
                        SELECT i.symbol, c.ts, c.open, c.close FROM candles c JOIN instruments i ON i.id = c.instrument_id
                        WHERE i.market IN ('NASDAQ', 'NYSE') AND c.timeframe = '1d' AND i.symbol = ANY(:syms)
                        ORDER BY i.symbol, c.ts
                        """
                    ),
                    {"syms": syms[i : i + 300]},
                )
                cur: str | None = None
                start = 0
                for sym, ts, o, c in rows:
                    s = str(sym)
                    if s != cur:
                        if cur is not None:
                            self.off[cur] = (start, len(days) - start)
                        cur, start = s, len(days)
                    days.append(ts.astimezone(UTC).toordinal())
                    cl = float(c)
                    op = float(o) if o is not None and float(o) > 0 else cl
                    opens.append(op)
                    closes.append(cl)
                if cur is not None:
                    self.off[cur] = (start, len(days) - start)
        engine.dispose()
        self.days = np.asarray(days, dtype=np.int64)
        self.o = np.asarray(opens, dtype=np.float32)
        self.c = np.asarray(closes, dtype=np.float32)

    def has(self, sym: str) -> bool:
        return sym in self.off

    def entry(self, sym: str, filed_ord: np.ndarray) -> np.ndarray:
        """접수일 다음 거래일의 전역 인덱스(없으면 −1).

        자료가 접수일보다 늦게 시작하면(토스 일봉 시작 전) 첫 봉을 돌려주면 안 된다 —
        접수일 뒤 `TOL` 일 안의 봉만 진입으로 친다.
        """
        a, n = self.off[sym]
        k = np.searchsorted(self.days[a : a + n], filed_ord, side="right")
        g = a + np.minimum(k, n - 1)
        g[(k >= n) | (self.days[g] - filed_ord > TOL)] = -1
        return g

    def at_or_after(self, sym: str, day_ord: np.ndarray) -> np.ndarray:
        """그 날 또는 그 뒤 첫 거래일의 전역 인덱스. 자료가 그 날보다 `TOL` 일 넘게 먼저 끝나면 −1."""
        a, n = self.off[sym]
        k = np.searchsorted(self.days[a : a + n], day_ord, side="left")
        g = a + np.minimum(k, n - 1)
        g[day_ord - self.days[g] > TOL] = -1
        return g

    def ret(self, g_in: np.ndarray, g_out: np.ndarray) -> np.ndarray:
        """시가 진입 → 종가 청산 % (비용 전). 무효(−1 · 범위 밖 · 역순)는 NaN."""
        ok = (g_in >= 0) & (g_out > g_in) & (g_out < len(self.days))
        r = np.full(len(g_in), np.nan)
        r[ok] = (self.c[g_out[ok]] / self.o[g_in[ok]] - 1) * 100
        return r


def load_tickers() -> dict[str, str]:
    p = CACHE / "cusip_figi.json"
    if not p.exists():
        return {}
    figi = cast("dict[str, dict[str, Any]]", json.loads(p.read_text(encoding="utf-8")))
    return {
        c: str(v["ticker"]).upper()
        for c, v in figi.items()
        if v.get("ticker") and v.get("exch") == "US"
    }


def load_events(
    tick: dict[str, str],
) -> tuple[list[Event], dict[str, float], dict[str, list[tuple[date, list[str]]]], dict[str, int]]:
    """사건 목록 · 보고자 교체율 · 접수일별 상위 10 CUSIP · 티커 없는 사건 수(보고자별)."""
    events: list[Event] = []
    turnover: dict[str, float] = {}
    tops: dict[str, list[tuple[date, list[str]]]] = {}
    unmapped: dict[str, int] = {}
    for path in sorted((CACHE / "13f").glob("*.json")):
        d = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
        cik = str(d["cik"])
        reps = sorted(
            cast("list[dict[str, Any]]", d["reports"]),
            key=lambda r: (str(r.get("period") or ""), str(r["filed"])),
        )
        rates: list[float] = []
        tops[cik] = []
        raw: list[tuple[date, date | None, str, str]] = []  # (filed, next, cusip, kind)
        for i, rep in enumerate(reps):
            filed = date.fromisoformat(str(rep["filed"]))
            nxt = date.fromisoformat(str(reps[i + 1]["filed"])) if i + 1 < len(reps) else None
            cur = {
                str(h["cusip"]): h
                for h in cast("list[dict[str, Any]]", rep["holdings"])
                if not h.get("put_call")
            }
            ranked = sorted(cur.items(), key=lambda kv: -float(kv[1]["value_usd"]))
            tops[cik].append((filed, [c for c, _h in ranked[:TOP_HOLD]]))
            if i == 0:
                continue
            prev = {
                str(h["cusip"]): h
                for h in cast("list[dict[str, Any]]", reps[i - 1]["holdings"])
                if not h.get("put_call")
            }
            changed = 0
            for cusip, h in cur.items():
                p = prev.get(cusip)
                if p is None:
                    kind = "new"
                else:
                    hs, ps = float(h["shares"]), float(p["shares"])
                    if hs == ps:
                        continue
                    ratio = hs / ps if ps else 9.0
                    hv, pv = float(h["value_usd"]), float(p["value_usd"])
                    vratio = hv / pv if pv else 9.0
                    if (ratio < SPLIT_SHARE[0] or ratio > SPLIT_SHARE[1]) and abs(
                        vratio - 1
                    ) < SPLIT_VALUE:
                        continue  # 분할 의심 — 사건 아님
                    kind = "added" if ratio > 1 else "reduced"
                changed += 1
                raw.append((filed, nxt, cusip, kind))
            for cusip in prev:
                if cusip not in cur:
                    changed += 1
                    raw.append((filed, nxt, cusip, "exited"))
            rates.append(changed / max(1, len(cur) + len(prev)) * 2)
        turnover[cik] = st.fmean(rates[-8:]) if rates else 0.0
        # SELL-FLAT — 같은 CUSIP 의 다음 줄임 · 전량 매도 접수일
        sells: dict[str, list[date]] = defaultdict(list)
        for filed, _n, cusip, kind in raw:
            if kind in ("reduced", "exited"):
                sells[cusip].append(filed)
        miss = 0
        for filed, nxt, cusip, kind in raw:
            t = tick.get(cusip)
            if not t:
                miss += 1
                continue
            flat = None
            if kind in ("new", "added"):
                flat = next((s for s in sells.get(cusip, ()) if s > filed), None)
            events.append(Event(cik, filed, nxt, flat, cusip, t, kind))
        unmapped[cik] = miss
    return events, turnover, tops, unmapped


def tercile(turnover: dict[str, float]) -> dict[str, str]:
    vals = sorted(turnover.values())
    if len(vals) < 3:
        return dict.fromkeys(turnover, "전체")
    q1, q2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    return {
        cik: ("저회전" if v < q1 else "중회전" if v < q2 else "고회전")
        for cik, v in turnover.items()
    }


def window_of(d: date) -> str | None:
    for name, a, z in WINDOWS:
        if a <= d < z:
            return name
    return None


@dataclass(slots=True)
class Leg:
    """규칙 하나를 사건 전부에 적용한 결과(배열은 사건 순)."""

    filed_ord: np.ndarray
    g_in: np.ndarray
    g_out: np.ndarray
    net: np.ndarray  # 순 % (NaN = 무효)
    spy: np.ndarray  # 같은 기간 SPY % (NaN = 없음)
    grp: np.ndarray  # 회전율 묶음(객체 배열)
    win: np.ndarray  # 구간 이름(객체 배열 · None 가능)
    sym: np.ndarray  # 티커(객체 배열)


def apply_rule(rule: str, ev: list[Event], px: Px, terc: dict[str, str]) -> Leg:
    n = len(ev)
    filed_ord = np.fromiter((e.filed.toordinal() for e in ev), dtype=np.int64, count=n)
    g_in = np.full(n, -1, dtype=np.int64)
    g_out = np.full(n, -1, dtype=np.int64)
    by_sym: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(ev):
        by_sym[e.ticker].append(i)
    for sym, idx in by_sym.items():
        ix = np.asarray(idx, dtype=np.int64)
        g_in[ix] = px.entry(sym, filed_ord[ix])
        a, m = px.off[sym]
        if rule in ("BUY-Q", "SELL-Q"):
            nxt = np.asarray(
                [(d.toordinal() if (d := ev[i].next_filed) else -1) for i in idx], dtype=np.int64
            )
            out = px.at_or_after(sym, np.where(nxt < 0, 0, nxt))
            out[nxt < 0] = -1
            g_out[ix] = out
        elif rule == "SELL-FLAT":
            flat = np.asarray(
                [(d.toordinal() if (d := ev[i].flat_at) else -1) for i in idx], dtype=np.int64
            )
            out = px.at_or_after(sym, np.where(flat < 0, 0, flat))
            out[flat < 0] = a + m - 1  # 아직 보유 → 자료 끝 종가
            g_out[ix] = out
        else:
            out = g_in[ix] + HOLDS[rule]
            out[(g_in[ix] < 0) | (out >= a + m)] = -1
            g_out[ix] = out
    side = -1.0 if rule == "SELL-Q" else 1.0
    net = side * px.ret(g_in, g_out) - COST
    spy = np.full(n, np.nan)
    if px.has("SPY"):
        s_in = px.entry("SPY", filed_ord)
        valid = (g_in >= 0) & (g_out > g_in) & (g_out < len(px.days))
        exit_ord = np.where(valid, px.days[np.clip(g_out, 0, len(px.days) - 1)], 0)
        s_out = px.at_or_after("SPY", exit_ord)
        s_out[~valid] = -1
        spy = px.ret(s_in, s_out)
    grp = np.asarray([terc.get(e.cik, "전체") for e in ev], dtype=object)
    win = np.asarray([window_of(e.filed) for e in ev], dtype=object)
    sym_arr = np.asarray([e.ticker for e in ev], dtype=object)
    return Leg(filed_ord, g_in, g_out, net, spy, grp, win, sym_arr)


def random_control(
    px: Px,
    leg: Leg,
    mask: np.ndarray,
    side: float,
    rng: np.random.Generator,
    win: tuple[date, date],
) -> float:
    """같은 종목 · 같은 구간 안 무작위 진입일 · 같은 보유 길이 → 평균 순 % 의 90분위."""
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return float("nan")
    if len(idx) > RANDOM_MAX_EVENTS:
        idx = rng.choice(idx, RANDOM_MAX_EVENTS, replace=False)
    hold = leg.g_out[idx] - leg.g_in[idx]
    lo = np.empty(len(idx), dtype=np.int64)
    hi = np.empty(len(idx), dtype=np.int64)
    a_ord, z_ord = win[0].toordinal(), win[1].toordinal()
    cache: dict[str, tuple[int, int]] = {}
    for j, i in enumerate(idx):
        s = str(leg.sym[i])
        if s not in cache:
            a, n = px.off[s]
            d = px.days[a : a + n]
            cache[s] = (a + int(np.searchsorted(d, a_ord)), a + int(np.searchsorted(d, z_ord)))
        lo[j], hi[j] = cache[s]
    span = hi - hold - lo  # 보유 길이를 품을 수 있는 시작 자리 수
    ok = span > 0
    if not ok.any():
        return float("nan")
    lo, span, hold = lo[ok], span[ok], hold[ok]
    means = np.empty(RANDOM_ROUNDS)
    for r in range(RANDOM_ROUNDS):
        g_in = lo + (rng.random(len(lo)) * span).astype(np.int64)
        means[r] = float(np.nanmean(side * px.ret(g_in, g_in + hold) - COST))
    return float(np.quantile(means, 0.9))


def block_ci(
    filed_ord: np.ndarray, net: np.ndarray, rng: np.random.Generator
) -> tuple[float, float]:
    """접수일 20일 블록 부트스트랩 80% CI."""
    keys, inv = np.unique(filed_ord // 20, return_inverse=True)
    if len(keys) < 3:
        return (float("nan"), float("nan"))
    sums = np.bincount(inv, weights=net)
    cnts = np.bincount(inv).astype(float)
    means = np.empty(1000)
    for r in range(1000):
        pick = rng.integers(0, len(keys), len(keys))
        means[r] = sums[pick].sum() / cnts[pick].sum()
    return float(np.quantile(means, 0.1)), float(np.quantile(means, 0.9))


def fmt(x: float, unit: str = "%") -> str:
    return "—" if x != x else f"{x:+.2f}{unit}"


def judge_cells(
    leg: Leg, side: float, px: Px, rng: np.random.Generator, lines: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    """구간 x 회전율 묶음마다 사전 등록 판정(§3) 넷을 재고 표 줄을 붙인다 — 모든 규칙이 같은 함수."""
    cells: dict[str, dict[str, dict[str, Any]]] = {}
    for wname, wa, wz in WINDOWS:
        for grp in GROUPS:
            mask = (leg.win == wname) & ~np.isnan(leg.net)
            if grp != "전체":
                mask &= leg.grp == grp
            n = int(mask.sum())
            if n == 0:
                lines.append(f"| {wname} | {grp} | 0 | — | — | — | — | — | — | — |")
                continue
            net = leg.net[mask]
            ex_mask = mask & ~np.isnan(leg.spy)
            ex = (
                float(np.mean(leg.net[ex_mask] - leg.spy[ex_mask]))
                if ex_mask.any()
                else float("nan")
            )
            q90 = random_control(px, leg, mask, side, rng, (wa, wz))
            ci = (
                block_ci(leg.filed_ord[mask], net, rng)
                if wname == CI_WINDOW
                else (float("nan"), float("nan"))
            )
            mean = float(net.mean())
            ok = (
                n >= MIN_N
                and mean > 0
                and ex > 0
                and mean > q90
                and (wname != CI_WINDOW or ci[0] > 0)
            )
            cells.setdefault(grp, {})[wname] = {
                "n": n,
                "net": mean,
                "ex": ex,
                "q90": q90,
                "ci": ci,
                "ok": ok,
            }
            ci_s = f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if wname == CI_WINDOW else "—"
            lines.append(
                f"| {wname} | {grp} | {n:,} | {mean:+.2f}% | {float(np.median(net)):+.2f}% | {float((net > 0).mean()) * 100:.0f}% | "
                f"{fmt(ex, '%p')} | {fmt(q90)} | {ci_s} | {'✅' if ok else '⛔'} |"
            )
    return cells


def write_realized(ev: list[Event], leg: Leg, px: Px) -> None:
    """화면 "그때 샀다면" — BUY-Q 사건별 실현값(다음 접수일까지) + 지금까지(자료 끝 종가)."""
    now_out = np.asarray(
        [px.off[e.ticker][0] + px.off[e.ticker][1] - 1 for e in ev], dtype=np.int64
    )
    now = px.ret(leg.g_in, now_out) - COST
    realized: dict[str, dict[str, dict[str, Any]]] = {}
    for i, e in enumerate(ev):
        if leg.g_in[i] < 0:
            continue
        realized.setdefault(e.cik, {}).setdefault(e.filed.isoformat(), {})[e.cusip] = {
            "ticker": e.ticker,
            "entry": round(float(px.o[leg.g_in[i]]), 4),
            "entry_date": date.fromordinal(int(px.days[leg.g_in[i]])).isoformat(),
            "ret_q": None if np.isnan(leg.net[i]) else round(float(leg.net[i]), 2),
            "ret_now": None if np.isnan(now[i]) else round(float(now[i]), 2),
            "kind": e.kind,
        }
    (CACHE / "buyq_realized.json").write_text(
        json.dumps(realized, ensure_ascii=False), encoding="utf-8"
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tick = load_tickers()
    events, turnover, tops, unmapped = load_events(tick)
    terc = tercile(turnover)
    top_tickers = {t for cik in tops for _f, cs in tops[cik] for c in cs if (t := tick.get(c))}
    tickers = {e.ticker for e in events} | top_tickers | {"SPY"}
    log(f"사건 {len(events):,} · 보고자 {len(turnover)} · 티커 {len(tickers):,} · 일봉 읽는 중")
    px = Px(tickers)
    have = {t for t in tickers if px.has(t)}
    log(f"일봉 있음 {len(have):,}/{len(tickers):,} · 봉 {len(px.days):,}")
    rng = np.random.default_rng(443)
    total_unmapped = sum(unmapped.values())
    # 생존 편향 크기 — 구간별로 가격이 있는 사건 비율(티커 없는 사건은 분모에 넣는다 · 보고자별 수라 구간 몫은 비례 배분 안 함)
    cover: list[str] = []
    for wname, _wa, _wz in WINDOWS:
        ev_w = [e for e in events if window_of(e.filed) == wname]
        priced = sum(1 for e in ev_w if px.has(e.ticker))
        cover.append(f"{wname} {priced:,}/{len(ev_w):,}({priced / max(1, len(ev_w)) * 100:.0f}%)")
    lines = [
        "# T443 따라 사기 백테스트 — 결과 (사전 등록 자 · 왕복 0.10% · 진입 = 접수일 다음 거래일 시가 · 청산 종가)",
        "",
        f"- 사건 {len(events):,}(티커 없는 사건 {total_unmapped:,} 제외) · 보고자 {len(turnover)} · 티커 {len(tickers):,} 중 일봉 있음 {len(have):,}({len(have) / max(1, len(tickers)) * 100:.0f}%) · 일봉 {len(px.days):,}봉 · SPY {'있음' if px.has('SPY') else '🔴 없음(SPY 초과 계산 불가)'}",
        f"- 회전율 삼분위: {' · '.join(f'{g} {sum(1 for v in terc.values() if v == g)}' for g in GROUPS[1:])} · 교체율 중앙 {st.median(turnover.values()) * 100:.0f}%"
        if turnover
        else "- 보고자 없음",
        f"- 🔴 생존 편향 — 가격 있는 사건(티커 있는 사건 중): {' · '.join(cover)} · 티커 없는 사건 {total_unmapped:,} 은 분모 밖. 상장 폐지 · 합병 종목은 토스에 없다(D2 1단계)",
        "- 판정 ✅ = 세 구간 모두 순 > 0 ∧ SPY 초과 > 0 ∧ 무작위 대조 90분위 위 · n ≥ 100 · 2022 ~ 26 블록 부트스트랩 80% CI 하한 > 0",
        "",
    ]
    summary: dict[str, Any] = {
        "events": len(events),
        "tickers": len(tickers),
        "with_prices": len(have),
        "rules": {},
    }
    passed: list[str] = []
    for rule, kinds in RULES.items():
        ev = [e for e in events if e.kind in kinds and px.has(e.ticker)]
        leg = apply_rule(rule, ev, px, terc)
        side = -1.0 if rule == "SELL-Q" else 1.0
        note = " · 아직 보유는 자료 끝 종가로" if rule == "SELL-FLAT" else ""
        lines += [
            f"## {rule} (사건 {' · '.join(kinds)}{note})",
            "",
            "| 구간 | 묶음 | n | 평균 순 | 중앙 | 승률 | SPY 초과 | 무작위 90분위 | 80% CI | 판정 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        cells = judge_cells(leg, side, px, rng, lines)
        summary["rules"][rule] = cells
        for grp, ws in cells.items():
            if len(ws) == len(WINDOWS) and all(v["ok"] for v in ws.values()):
                passed.append(f"{rule} · {grp}")
        lines.append("")
        if rule == "BUY-Q":
            write_realized(ev, leg, px)
            per: dict[str, list[float]] = defaultdict(list)
            for i, e in enumerate(ev):
                if not np.isnan(leg.net[i]):
                    per[e.cik].append(float(leg.net[i]))
            rows = sorted(
                ((cik, len(v), st.fmean(v)) for cik, v in per.items() if len(v) >= 30),
                key=lambda r: -r[2],
            )
            lines += [
                "### BUY-Q 보고자별(n ≥ 30 · 전 구간 · 평균 순) — 위 10 · 아래 10",
                "",
                "| 보고자 CIK | n | 평균 순 | 묶음 |",
                "|---|---|---|---|",
            ]
            shown = rows if len(rows) <= 20 else rows[:10] + rows[-10:]
            for cik, cnt, m in shown:
                lines.append(f"| {cik} | {cnt:,} | {fmt(m)} | {terc.get(cik, '')} |")
            lines.append("")
        log(f"{rule} 끝 · n {len(ev):,}")
    # HOLD-12M — 접수일 상위 10 비중 · 252 거래일.
    # 2026-10-09 정정: 첫 실행은 이 규칙에만 무작위 대조 · 블록 부트스트랩을 빼먹었다(사전 등록 §3 은
    # 모든 규칙에 넷 다). 같은 판정 함수(`judge_cells`)로 돌린다.
    lines += [
        "## HOLD-12M (접수일 상위 10 비중 · 252 거래일)",
        "",
        "| 구간 | 묶음 | n | 평균 순 | 중앙 | 승률 | SPY 초과 | 무작위 90분위 | 80% CI | 판정 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    h_rows = [
        (cik, filed, t)
        for cik, reps in tops.items()
        for filed, cs in reps
        for c in cs
        if (t := tick.get(c)) and px.has(t)
    ]
    h_net = np.full(len(h_rows), np.nan)
    h_spy = np.full(len(h_rows), np.nan)
    h_gin = np.full(len(h_rows), -1, dtype=np.int64)
    h_gout = np.full(len(h_rows), -1, dtype=np.int64)
    by_sym: dict[str, list[int]] = defaultdict(list)
    for i, (_c, _f, t) in enumerate(h_rows):
        by_sym[t].append(i)
    h_filed = np.asarray([f.toordinal() for _c, f, _t in h_rows], dtype=np.int64)
    for sym, idx in by_sym.items():
        ix = np.asarray(idx, dtype=np.int64)
        g_in = px.entry(sym, h_filed[ix])
        g_out = g_in + HOLDS["HOLD-12M"]
        a, m = px.off[sym]
        g_out[(g_in < 0) | (g_out >= a + m)] = -1
        h_gin[ix] = g_in
        h_gout[ix] = g_out
        h_net[ix] = px.ret(g_in, g_out) - COST
        if px.has("SPY"):
            s_in = px.entry("SPY", h_filed[ix])
            s_out = s_in + HOLDS["HOLD-12M"]
            sa_, sm = px.off["SPY"]
            s_out[(s_in < 0) | (s_out >= sa_ + sm)] = -1
            h_spy[ix] = px.ret(s_in, s_out)
    h_win = np.asarray([window_of(f) for _c, f, _t in h_rows], dtype=object)
    h_grp = np.asarray([terc.get(c, "전체") for c, _f, _t in h_rows], dtype=object)
    h_sym = np.asarray([t for _c, _f, t in h_rows], dtype=object)
    h_leg = Leg(h_filed, h_gin, h_gout, h_net, h_spy, h_grp, h_win, h_sym)
    hold_cells = judge_cells(h_leg, 1.0, px, rng, lines)
    log("HOLD-12M 끝")
    summary["rules"]["HOLD-12M"] = hold_cells
    for grp, ws in hold_cells.items():
        if len(ws) == len(WINDOWS) and all(v["ok"] for v in ws.values()):
            passed.append(f"HOLD-12M · {grp}")
    lines += ["", "## 종합 — 세 구간 모두 ✅", "", (" · ".join(passed) if passed else "없음"), ""]
    summary["passed"] = passed
    (OUT / "result.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print("\n".join(lines))
    print("T443_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
