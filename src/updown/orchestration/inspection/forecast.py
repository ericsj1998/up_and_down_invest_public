"""보유 매매의 예상 청산 띠 · 앞날 부채꼴 · 갈래 확률 — **표시 전용** 계산 (T820 · T825).

연구 매매에서 맞춘 모형 값(`config/forecast/` · 비공개)을 읽어, 지금 열린 매매 하나의 처지
(보유 시간 · 처음 손절 기준 지금 R · 진입 때 변동성 · BTC 국면 · 직전 움직임)로 모형을 돌린다.

    띠       표(보유 시간 칸 x 지금 R 칸) 또는 선형 분위 — 청산 R 의 25 · 50 · 75 (+ 꼬리 90)
    부채꼴   조건 표 · 닮은 과거 길(가까운 k) · 로지스틱 섞음 — 12 · 24 · 48 시간 뒤 10 ~ 90 분위
    갈래     나무 부스팅 — 48 시간 뒤 내림 · 옆 · 오름 확률(온도 보정) + 다리 틀 선

Note:
    🔴 주문 · 청산 · 크기에 쓰지 않는다(절대 규칙 #2 · #4) — 화면이 그리고 사람이 읽는다.
    값의 정의는 모형을 맞춘 연구 코드와 한 글자도 다르지 않아야 한다. 비공개 맞춤 시험이 연구가
    낸 값과 대조한다. 정의를 바꾸려면 연구를 다시 재고 값을 다시 뽑는다 — 여기서 고치지 않는다.

    numpy 없이 순수 파이썬이다 — API 프로세스에 새 라이브러리를 올리지 않는다(서버 메모리).
    2026-11-11 측정: 한 번 계산 0.1 ~ 4 ms · 모형 값 합 약 1.5 MB · 다리 값은 처음 쓸 때 읽는다.
"""

from __future__ import annotations

import bisect
import heapq
import json
import math
import struct
import sys
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

DEFAULT_DIR = Path("config/forecast")
"""모형 값 폴더(비공개). 없으면 예측을 안 그린다."""

HOUR_MS = 3_600_000
STEP_H = 4
"""연구가 관측한 간격(진입 뒤 4 시간마다) — 검증된 시점."""

TIE = 1e-12


class ForecastError(ValueError):
    """모형 값이 깨졌거나 이 계산이 모르는 입력을 요구한다 — 조용히 넘기지 않는다(규칙 #8)."""


@dataclass(frozen=True, slots=True)
class Bar:
    """1 시간봉 하나(float) — `ts_ms` 는 봉 시작(UTC ms)."""

    ts_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float = 0.0


@dataclass(frozen=True, slots=True)
class Inputs:
    """한 시점의 입력 — 연구 관측 한 줄과 같은 칸.

    Attributes:
        hold_h: 진입 뒤 시간(연구 = 4 의 배수).
        cur_r: 방향 x (그 시각 1H 종가 ÷ 진입 기준가 - 1) ÷ 처음 손절 거리(%).
        atr_pct: 진입 봉까지 1H TR 평균(ATR_N 봉) ÷ 진입 기준가 x 100.
            모르면 None(적합 중위로 채움).
        btc_up: BTC 20 일 수익률(전날까지) ≥ 0 이면 1 · 아니면 0 · 모르면 0.5.
        mom4: 직전 4 시간 움직임(R). 모르면 None(0 으로 채움).
        mom12: 직전 12 시간 움직임(R). 모르면 None.
        extra: 나무 모형 입력(r1 ~ r6 · upn · streak · mfe · mae · give). 모르면 None(적합 중위).
    """

    hold_h: float
    cur_r: float
    atr_pct: float | None
    btc_up: float
    mom4: float | None
    mom12: float | None
    extra: Mapping[str, float | None] = field(default_factory=dict[str, float | None])

    def column(self, name: str) -> float | None:
        """나무 · 표준화 입력의 한 칸 — 연구 `features` 의 칸 이름 그대로."""
        base: dict[str, float | None] = {
            "lh": math.log1p(self.hold_h),
            "cur": self.cur_r,
            "atr": self.atr_pct,
            "btc": self.btc_up,
            "mom4": 0.0 if self.mom4 is None else self.mom4,
            "mom12": 0.0 if self.mom12 is None else self.mom12,
        }
        if name in base:
            return base[name]
        if name in self.extra:
            return self.extra[name]
        raise ForecastError(f"모르는 입력 칸: {name}")


TREE_COLS = ("r1", "r2", "r3", "r4", "r5", "r6", "upn", "streak", "mfe", "mae", "give")
"""이 모듈이 봉에서 낼 줄 아는 나무 입력(연구 ㉯ 묶음). 다른 칸을 요구하는 모형은 거절한다."""


def _streak_back(flags: Sequence[bool]) -> int:
    n = 0
    for v in reversed(flags):
        if not v:
            break
        n += 1
    return n


def inputs_from_bars(
    bars: Sequence[Bar],
    *,
    entry_ms: int,
    obs_ms: int,
    sign: int,
    stop_pct: float,
    btc_up: float,
    atr_n: int,
    look: int,
    want_tree: bool,
) -> Inputs | None:
    """닫힌 1H 봉(시각 오름차순)에서 연구와 같은 입력을 낸다.

    Args:
        bars: 닫힌 1H 봉. 진입 봉과 관측 봉이 들어 있어야 한다.
        entry_ms: 진입 시각(1H 경계) — 진입 기준가는 그 직전에 닫힌 봉의 종가.
        obs_ms: 관측 시각(1H 경계) — 그 직전에 닫힌 봉까지 쓴다.
        sign: 롱 1 · 숏 -1.
        stop_pct: 처음 손절 거리(%) — 1R.
        btc_up: BTC 국면(1 · 0 · 0.5).
        atr_n: 진입 ATR 봉 수(연구 56).
        look: 나무 입력을 믿으려면 끊김 없이 이어져야 하는 봉 수(연구 600).
        want_tree: 나무 입력(`TREE_COLS`)도 낼지.

    Returns:
        입력. 진입 봉이나 관측 봉이 창에 없으면 None(그리지 않는다).
    """
    if stop_pct <= 0 or not bars:
        return None
    index = {b.ts_ms: i for i, b in enumerate(bars)}
    i0 = index.get(entry_ms - HOUR_MS)
    j = index.get(obs_ms - HOUR_MS)
    if i0 is None or j is None or j < i0:
        return None
    e = bars[i0].c
    if e <= 0:
        return None
    c = [b.c for b in bars]
    hold = (obs_ms - entry_ms) / HOUR_MS
    cur = sign * (c[j] / e - 1) * 100 / stop_pct
    # 진입 ATR — 연구는 자료 첫 봉의 앞 종가를 자기 종가로 뒀다. 창 첫 봉이 들어가면 값이 갈리므로
    #   창 안에 앞 종가까지 있을 때만 낸다(없으면 적합 중위로 채운다 · 연구의 빈 값과 같은 길).
    atr: float | None = None
    if i0 - atr_n + 1 >= 1:
        tot = 0.0
        for i in range(i0 - atr_n + 1, i0 + 1):
            b, pc = bars[i], c[i - 1]
            tot += max(b.h - b.low, max(abs(b.h - pc), abs(b.low - pc)))
        atr = tot / atr_n / e * 100

    def mom(n: int) -> float | None:
        q = j - n
        if q < 0 or bars[q].ts_ms != bars[j].ts_ms - n * HOUR_MS or c[q] <= 0:
            return None
        return sign * (c[j] / c[q] - 1) * 100 / stop_pct

    extra: dict[str, float | None] = {}
    if want_tree:
        extra = _tree_inputs(bars, c, i0, j, e, sign, stop_pct, cur, look)
    return Inputs(
        hold_h=hold,
        cur_r=cur,
        atr_pct=atr,
        btc_up=btc_up,
        mom4=mom(4),
        mom12=mom(12),
        extra=extra,
    )


def _tree_inputs(
    bars: Sequence[Bar],
    c: Sequence[float],
    i0: int,
    j: int,
    e: float,
    sign: int,
    stop: float,
    cur: float,
    look: int,
) -> dict[str, float | None]:
    """연구 `sym_series` · `features` 의 ㉯ 묶음 — 관측 봉 `j` 한 칸만."""
    run = 0
    for k in range(j, 0, -1):
        if bars[k].ts_ms - bars[k - 1].ts_ms != HOUR_MS:
            break
        run += 1
    ok = run >= min(j, look) and j >= 7  # 4H 시가 · 앞 4H 시가가 있어야 한다(nan4)

    def o4(i: int) -> float | None:
        return bars[i - 3].o if i >= 3 else None

    def up_dn(i: int) -> tuple[bool, bool]:
        o = o4(i)
        if o is None:
            return False, False
        return c[i] > o, c[i] < o

    out: dict[str, float | None] = dict.fromkeys(TREE_COLS)
    if ok:
        for k in range(1, 7):
            a, b = j - 4 * (k - 1), j - 4 * k
            if b >= 0 and c[b] > 0:
                out[f"r{k}"] = sign * (c[a] / c[b] - 1) * 100 / stop
        phase = list(range(j % 4, j + 1, 4))
        ups = [up_dn(i)[0] for i in phase]
        dns = [up_dn(i)[1] for i in phase]
        if len(phase) >= 6:
            up6, dn6 = sum(ups[-6:]) / 6, sum(dns[-6:]) / 6
            out["upn"] = up6 if sign > 0 else dn6
        stk = max(-6, min(6, _streak_back(ups) - _streak_back(dns)))
        out["streak"] = float(sign * stk)
    mf = ma = 0.0
    if j > i0:
        hi = max(b.h for b in bars[i0 + 1 : j + 1])
        lo = min(b.low for b in bars[i0 + 1 : j + 1])
        up_, dn_ = (hi / e - 1) * 100 / stop, (lo / e - 1) * 100 / stop
        mf, ma = (up_, dn_) if sign > 0 else (-dn_, -up_)
    mf, ma = max(mf, 0.0), min(ma, 0.0)
    out["mfe"], out["mae"], out["give"] = mf, ma, cur - mf
    return out


def btc_regime(day_closes: Sequence[tuple[int, float]], entry_day_ms: int) -> float:
    """BTC 국면 — 전날까지 20 일 수익률 ≥ 0 이면 1 · 아니면 0 · 모르면 0.5(연구 `btc_regime`).

    Args:
        day_closes: (UTC 날 시작 ms, 그날 마지막 4H 종가) — 날 오름차순 · 봉이 있는 날만
            (연구 `resample().last().dropna()`).
        entry_day_ms: 진입한 UTC 날 시작(ms).

    Returns:
        1 · 0 · 0.5.
    """
    days = [d for d, _ in day_closes]
    i = bisect.bisect_left(days, entry_day_ms)
    if i >= len(days) or days[i] != entry_day_ms:
        return 0.5
    if i - 21 < 0:
        return 0.5
    a, b = day_closes[i - 1][1], day_closes[i - 21][1]
    if b <= 0:
        return 0.5
    return 1.0 if a / b - 1 >= 0 else 0.0


def _wq(vals: Sequence[float], ws: Sequence[float], qs: Sequence[float]) -> list[float]:
    """무게 분위 — 연구 `wq`(안정 정렬 · 누적 무게 ≥ q - 1e-12 인 첫 값)."""
    order = sorted(range(len(vals)), key=vals.__getitem__)
    tot = sum(ws)
    cum: list[float] = []
    acc = 0.0
    for i in order:
        acc += ws[i]
        cum.append(acc / tot)
    out: list[float] = []
    for q in qs:
        pos = next((p for p, cw in enumerate(cum) if cw >= q - TIE), 0)
        out.append(vals[order[pos]])
    return out


def _softmax(raw: Sequence[float]) -> list[float]:
    mx = max(raw)
    ex = [math.exp(v - mx) for v in raw]
    s = sum(ex)
    return [v / s for v in ex]


def _temper(p: Sequence[float], t: float) -> list[float]:
    q = [min(max(v, 1e-4), 1.0) ** (1.0 / t) for v in p]
    s = sum(q)
    return [v / s for v in q]


def read_bin(path: Path) -> dict[str, array[Any]]:
    """숫자 배열 묶음 — 머리(JSON: 이름 → [형 · 길이]) + 배열 바이트(little endian)."""
    if sys.byteorder != "little":  # pragma: no cover - 서버 · 개발기는 다 little
        raise ForecastError("배열 파일은 little endian 기계에서만 읽는다")
    raw = path.read_bytes()
    (size,) = struct.unpack_from("<I", raw, 0)
    head: dict[str, list[Any]] = json.loads(raw[4 : 4 + size])
    pos = 4 + size
    out: dict[str, array[Any]] = {}
    for name, (code, n) in head.items():
        a: array[Any] = cast("Any", array)(code)  # 형 코드는 파일 머리가 정한다
        width = a.itemsize * int(n)
        a.frombytes(raw[pos : pos + width])
        pos += width
        out[name] = a
    if pos != len(raw):
        raise ForecastError(f"배열 파일 길이가 머리와 다르다: {path.name}")
    return out


class LegModel:
    """다리 하나의 띠 · 부채꼴 · 갈래 — 큰 배열은 처음 쓸 때 읽는다."""

    def __init__(self, name: str, spec: Mapping[str, Any], folder: Path) -> None:
        """다리 값(`forecast.json` 의 한 칸)을 받는다 — 모르는 나무 입력 칸이면 터진다."""
        self.name = name
        self.sign = int(spec["sign"])
        self.band: Mapping[str, Any] = spec["band"]
        self.fan: Mapping[str, Any] = spec["fan"]
        self.branch: Mapping[str, Any] | None = spec.get("branch")
        self._folder = folder
        self._near: dict[str, array[Any]] | None = None
        self._trees: dict[str, array[Any]] | None = None
        if self.branch is not None:
            unknown = [c for c in self.branch["cols"] if c not in TREE_COLS and c not in _BASE]
            if unknown:
                raise ForecastError(f"{name} 갈래 모형이 이 모듈이 모르는 입력을 쓴다: {unknown}")

    @property
    def wants_tree(self) -> bool:
        """나무 입력(`TREE_COLS`)이 필요한가."""
        return self.branch is not None and any(c in TREE_COLS for c in self.branch["cols"])

    # ── 띠 ──────────────────────────────────────────────────────────────────
    def band_r(self, x: Inputs) -> list[float]:
        """청산 R 분위(연구 `Table.predict` · `QReg.predict`) — 오름차순."""
        b = self.band
        if b["kind"] == "table":
            if x.hold_h == 0:
                return list(b["base"])
            hb = bisect.bisect_right(b["hold_edges"], x.hold_h)
            rb = bisect.bisect_right(b["r_edges"], x.cur_r)
            got = b["cells"].get(f"{hb}:{rb}") or b["rows"].get(str(rb)) or b["base"]
            return list(got)
        atr = b["atr_fill"] if x.atr_pct is None else x.atr_pct
        feats = (math.log1p(x.hold_h), x.cur_r, atr, x.btc_up)
        qs = [row[0] + sum(w * v for w, v in zip(row[1:], feats, strict=True)) for row in b["coef"]]
        return sorted(qs)

    # ── 부채꼴 ──────────────────────────────────────────────────────────────
    def _z(self, x: Inputs) -> list[float]:
        f = self.fan
        atr = f["atr0"] if x.atr_pct is None else x.atr_pct
        raw = (
            math.log1p(x.hold_h),
            x.cur_r,
            atr,
            x.btc_up,
            0.0 if x.mom4 is None else x.mom4,
            0.0 if x.mom12 is None else x.mom12,
        )
        return [(v - m) / s for v, m, s in zip(raw, f["mu"], f["sd"], strict=True)]

    def fan_r(self, x: Inputs) -> list[list[float]]:
        """지금 대비 변화 R — [분위][시점](연구 `fan` 꼴 그대로)."""
        f = self.fan
        kind = f["kind"]
        if kind == "cells":
            if x.hold_h == 0:
                cell, row = 1, 0
            else:
                hb = bisect.bisect_right(f["hold_edges"], x.hold_h)
                rb = bisect.bisect_right(f["r_edges"], x.cur_r)
                cell, row = 100 + hb * 10 + rb, 10 + rb
            cells = f["cells"]
            key = str(cell) if str(cell) in cells else (str(row) if str(row) in cells else "0")
            return [list(r) for r in cells[key]]
        z = self._z(x)
        if kind == "logit":
            feats = [*z, z[1] ** 2]
            logits = [
                b0 + sum(w * v for w, v in zip(ws, feats, strict=True))
                for b0, ws in zip(f["intercept"], f["coef"], strict=True)
            ]
            p = _softmax(logits)
            out = [[0.0] * len(f["cdfs"]) for _ in f["qs"]]
            for jj, cd in enumerate(f["cdfs"]):
                g, rows = cd["g"], cd["cdf"]
                mix = [sum(p[b] * rows[b][i] for b in range(3)) for i in range(len(g))]
                for qi, q in enumerate(f["qs"]):
                    pos = next((i for i, m in enumerate(mix) if m >= q - TIE), 0)
                    out[qi][jj] = g[pos]
            return out
        if kind == "near":
            return self._near_fan(z)
        raise ForecastError(f"모르는 부채꼴 꼴: {kind}")

    def _near_fan(self, z: Sequence[float]) -> list[list[float]]:
        f = self.fan
        if self._near is None:
            self._near = read_bin(self._folder / f["file"])
        zs, tid = self._near["z"], self._near["tid"]
        n, nf, k = int(f["n"]), len(z), int(f["k"])
        dist = [0.0] * n
        for i in range(n):
            base = i * nf
            s = 0.0
            for col in range(nf):
                d = zs[base + col] - z[col]
                s += d * d
            dist[i] = s
        idx = heapq.nsmallest(k, range(n), key=dist.__getitem__)
        cnt: dict[int, int] = {}
        for i in idx:
            cnt[tid[i]] = cnt.get(tid[i], 0) + 1
        ws = [1.0 / cnt[tid[i]] for i in idx]
        steps = [name for name in self._near if name.startswith("pc")]
        cols = [_wq([self._near[s][i] for i in idx], ws, f["qs"]) for s in steps]
        return [[cols[jj][qi] for jj in range(len(cols))] for qi in range(len(f["qs"]))]

    # ── 갈래 ────────────────────────────────────────────────────────────────
    def branch_p(self, x: Inputs) -> list[float] | None:
        """48 시간 뒤 내림 · 옆 · 오름 확률(온도 보정 뒤) — 갈래 모형이 없는 다리는 None."""
        br = self.branch
        if br is None:
            return None
        if self._trees is None:
            self._trees = read_bin(self._folder / br["file"])
        t = self._trees
        row = [x.column(c) for c in br["cols"]]
        vec = [m if v is None or v != v else v for v, m in zip(row, br["med"], strict=True)]
        feat, left, right, val, miss, start = (
            t["feat"],
            t["left"],
            t["right"],
            t["val"],
            t["miss"],
            t["start"],
        )
        ncls = int(br["classes"])
        raw = list(t["base"])
        for n, s in enumerate(start):
            i = 0
            while True:
                fi = feat[s + i]
                if fi < 0:
                    raw[n % ncls] += val[s + i]
                    break
                v = vec[fi]
                if v != v:
                    i = left[s + i] if miss[s + i] else right[s + i]
                else:
                    i = left[s + i] if v <= val[s + i] else right[s + i]
        return _temper(_softmax(raw), float(br["temperature"]))


_BASE = ("lh", "cur", "atr", "btc", "mom4", "mom12")


class Forecaster:
    """모형 값 폴더 하나 — 다리 이름(화면 짧은 이름) → `LegModel`."""

    def __init__(self, folder: Path = DEFAULT_DIR) -> None:
        """폴더의 `forecast.json` 을 읽는다(큰 배열은 다리가 처음 쓸 때)."""
        doc = json.loads((folder / "forecast.json").read_text(encoding="utf-8"))
        self.version: str = str(doc["version"])
        self.atr_n = int(doc["atr_bars"])
        self.look = int(doc["look"])
        self.legs = {name: LegModel(name, spec, folder) for name, spec in doc["legs"].items()}

    @classmethod
    def load(cls, folder: Path = DEFAULT_DIR) -> Forecaster | None:
        """폴더가 없으면 None(공개 빌드 · 예측 안 그림). 깨졌으면 터진다(규칙 #8)."""
        if not (folder / "forecast.json").exists():
            return None
        return cls(folder)


def price_of(e: float, sign: int, stop_pct: float, r: float) -> float:
    """진입 대비 R → 가격(연구 띠 정의: 진입 기준가 x (1 + 방향 x R x 손절%))."""
    return e * (1 + sign * r * stop_pct / 100)
