"""백테스트 리포트 차트용 자료 읽기 — 합성 45미래 상세 · E1 백테스트 (T222 §5 · 2026-09-06).

연구 PC 의 `scenario_paths.py` · `backtest_paths.py` 가 남긴 **JSON 머리 + 원시 배열**
(`.f32` float32 LE · `.i32` int32 LE)을 읽어 화면이 그릴 모양(봉 · 매매 · 자본 곡선)으로
자른다. 런타임은 numpy 를 들이지 않는다(격리 규칙) — 표준 `array` 와 슬라이스만 쓴다.
파일은 프로세스에 한 번 올라온다(합성 상세 ~7 MB · E1 ~2 MB).

이 모듈은 읽기만 한다 — 계산(지표 · 통계)은 없다. 지표는 화면이 그린다(사용자가 설정을
바꾸며 본다).
"""

from __future__ import annotations

import json
import math
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


def _array(path: Path, code: str) -> array[float] | array[int]:
    """원시 배열 파일(리틀엔디언)을 `array` 로 — 없으면 빈 배열.

    파일은 연구 PC 가 LE 로 남기므로 빅엔디언 호스트에서만 바이트를 뒤집는다. 없는 파일을
    빈 배열로 두는 것은 `.i32` 가 없는 산출물(매매 없음)이 정상이기 때문이다.

    Args:
        path: `.f32` · `.i32` 파일.
        code: `array` 타입코드 (`"f"` · `"i"`).

    Returns:
        읽은 배열.
    """
    arr: array[Any] = array(code)
    if path.exists():
        arr.frombytes(path.read_bytes())
        if sys.byteorder == "big":
            arr.byteswap()
    return arr


@dataclass(frozen=True)
class Store:
    """JSON 머리 + 배열 둘. `head` 는 그대로 노출한다 — 화면이 필요한 메타를 고른다."""

    head: dict[str, Any]
    f32: array[float]
    i32: array[int]

    @classmethod
    def load(cls, json_path: Path) -> Store:
        """`x.json` 을 읽고 옆의 `x.f32` · `x.i32`(없으면 빈 배열)를 함께 올린다.

        Args:
            json_path: 머리 파일 경로.

        Returns:
            머리와 원시 배열을 든 저장소.
        """
        head: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
        f32 = cast("array[float]", _array(json_path.with_suffix(".f32"), "f"))
        i32 = cast("array[int]", _array(json_path.with_suffix(".i32"), "i"))
        return cls(head=head, f32=f32, i32=i32)

    def floats(self, offset: int, count: int) -> list[float]:
        """f32 배열의 [offset, offset+count) 조각.

        Args:
            offset: 시작 위치.
            count: 길이.

        Returns:
            float 목록.
        """
        return list(self.f32[offset : offset + count])

    def ints(self, offset: int, count: int) -> list[int]:
        """i32 배열의 [offset, offset+count) 조각.

        Args:
            offset: 시작 위치.
            count: 길이.

        Returns:
            int 목록.
        """
        return list(self.i32[offset : offset + count])


@dataclass(frozen=True)
class Candles:
    """봉 — 열 단위 목록 (JSON 이 작다). 시각은 epoch 초."""

    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741 — OHLC 관례
    c: list[float]

    def as_json(self) -> dict[str, list[float] | list[int]]:
        """화면 응답 모양.

        Returns:
            `{t, o, h, l, c}` — 열 단위 배열 (행 단위보다 JSON 이 작다).
        """
        return {"t": self.t, "o": self.o, "h": self.h, "l": self.l, "c": self.c}


def _pick(
    ts: list[int], o: list[float], h: list[float], lo: list[float], c: list[float]
) -> Candles:
    """NaN(비는 날)은 뺀다 — 화면이 그리지 못하는 점을 보내지 않는다."""
    keep = [i for i in range(len(ts)) if not math.isnan(o[i])]
    if len(keep) == len(ts):
        return Candles(t=ts, o=o, h=h, l=lo, c=c)
    return Candles(
        t=[ts[i] for i in keep],
        o=[o[i] for i in keep],
        h=[h[i] for i in keep],
        l=[lo[i] for i in keep],
        c=[c[i] for i in keep],
    )


def _ohlc(store: Store, offset: int, bars: int, ts: list[int]) -> Candles:
    flat = store.floats(offset, bars * 4)
    return _pick(ts, flat[0::4], flat[1::4], flat[2::4], flat[3::4])


def grid(ts0: int, step: int, bars: int) -> list[int]:
    """균등 시각 격자.

    Args:
        ts0: 첫 시각 (epoch 초).
        step: 간격 (초).
        bars: 개수.

    Returns:
        `ts0 + i*step`.
    """
    return [ts0 + i * step for i in range(bars)]


# ── 합성 45미래 상세 ─────────────────────────────────────────────────────────


def synth_daily(store: Store, future: int, symbol: str) -> Candles:
    """미래 하나 · 종목 하나의 일봉.

    Args:
        store: 합성 상세 저장소.
        future: 미래 번호.
        symbol: 종목.

    Returns:
        일봉. 배치 `daily[futures][days][symbols][4]` 에서 잘라낸다.
    """
    head = store.head
    symbols: list[str] = head["symbols"]
    daily = head["daily"]
    days = int(daily["days"])
    n_sym = len(symbols)
    si = symbols.index(symbol)
    base = int(daily["f32_offset"]) + future * days * n_sym * 4
    # 종목 하나만 뽑는다 — 하루마다 stride 로 4개
    o: list[float] = []
    h: list[float] = []
    lo: list[float] = []
    c: list[float] = []
    f = store.f32
    for d in range(days):
        at = base + (d * n_sym + si) * 4
        o.append(f[at])
        h.append(f[at + 1])
        lo.append(f[at + 2])
        c.append(f[at + 3])
    return _pick(grid(int(daily["ts0"]), int(daily["step"]), days), o, h, lo, c)


def synth_windows(store: Store, future: int, symbol: str | None = None) -> list[dict[str, Any]]:
    """청산 전후 4h 창 — 그 미래(·종목)의 것만.

    Args:
        store: 합성 상세 저장소.
        future: 미래 번호.
        symbol: 종목. None 이면 전 종목.

    Returns:
        창 항목들 — 각 항목에 봉이 붙는다.
    """
    out: list[dict[str, Any]] = []
    for w in store.head.get("windows", []):
        if int(w["future"]) != future or (symbol and w["symbol"] != symbol):
            continue
        bars = int(w["bars"])
        ts = grid(int(w["ts0"]), int(w["step"]), bars)
        out.append(
            {
                "symbol": w["symbol"],
                "closed_ts": int(w["closed_ts"]),
                "step": int(w["step"]),
                "candles": _ohlc(store, int(w["f32_offset"]), bars, ts).as_json(),
            }
        )
    return out


def synth_liquidations(
    store: Store, future: int, symbol: str | None = None
) -> list[dict[str, Any]]:
    """그 미래(·종목)의 청산(reason == liq) 매매.

    Args:
        store: 합성 상세 저장소.
        future: 미래 번호.
        symbol: 종목. None 이면 전 종목.

    Returns:
        진입·손절선·청산 시각과 가격을 담은 행들.
    """
    return [
        dict(row)
        for row in store.head.get("liquidations", [])
        if int(row["future"]) == future and (not symbol or row["symbol"] == symbol)
    ]


def synth_stats(store: Store, future: int) -> dict[str, Any]:
    """그 미래의 매매 통계 — 사유별 · 종목별 수.

    Args:
        store: 합성 상세 저장소.
        future: 미래 번호.

    Returns:
        통계 행. 없으면 빈 사전.
    """
    for row in store.head.get("stats", []):
        if int(row["future"]) == future:
            return {k: v for k, v in row.items() if k != "future"}
    return {}


def synth_liquidation_summary(store: Store) -> dict[str, Any]:
    """45미래 전체의 강제청산 요약 — **전체 매매 대비 확률**과 종목별·시나리오별 분해 (2026-09-06).

    "청산 난 미래 31/45 · 53건" 만 보면 크게 읽힌다 — 분모(매매 수)를 붙인다. 매매 수는
    `stats[].by_reason` 의 합, 청산 행은 `liquidations`. 확률 = 청산 / 전체 매매 (%).

    Args:
        store: 합성 상세 저장소.

    Returns:
        전체 확률과 종목별·시나리오별 분해.
    """
    head = store.head
    liqs: list[dict[str, Any]] = head.get("liquidations", [])
    stats: list[dict[str, Any]] = head.get("stats", [])
    scenario: list[str] = head.get("scenario", [])
    trades_total = 0
    trades_by_symbol: dict[str, int] = {}
    for row in stats:
        for n in row.get("by_reason", {}).values():
            trades_total += int(n)
        for sym, n in row.get("trades_by_symbol", {}).items():
            trades_by_symbol[sym] = trades_by_symbol.get(sym, 0) + int(n)
    liq_by_symbol: dict[str, int] = {}
    futures_by_symbol: dict[str, set[int]] = {}
    liq_by_scenario: dict[str, int] = {}
    futures_by_scenario: dict[str, set[int]] = {}
    for row in liqs:
        sym = str(row["symbol"])
        k = int(row["future"])
        liq_by_symbol[sym] = liq_by_symbol.get(sym, 0) + 1
        futures_by_symbol.setdefault(sym, set()).add(k)
        name = scenario[k] if k < len(scenario) else "?"
        liq_by_scenario[name] = liq_by_scenario.get(name, 0) + 1
        futures_by_scenario.setdefault(name, set()).add(k)

    def pct(n: int, d: int) -> float:
        """백분율 — 분모 0 이면 0.

        Args:
            n: 분자.
            d: 분모.

        Returns:
            소수 셋째 자리까지.
        """
        return round(n / d * 100, 3) if d else 0.0

    by_symbol = [
        {
            "symbol": sym,
            "liquidations": liq_by_symbol.get(sym, 0),
            "trades": trades_by_symbol.get(sym, 0),
            "pct": pct(liq_by_symbol.get(sym, 0), trades_by_symbol.get(sym, 0)),
            "futures": len(futures_by_symbol.get(sym, ())),
        }
        for sym in head.get("symbols", [])
    ]
    by_symbol.sort(key=lambda r: (-int(r["liquidations"]), str(r["symbol"])))
    seen: list[str] = []
    for name in scenario:
        if name not in seen:
            seen.append(name)
    by_scenario = [
        {
            "scenario": name,
            "liquidations": liq_by_scenario.get(name, 0),
            "futures_hit": len(futures_by_scenario.get(name, ())),
            "futures": scenario.count(name),
        }
        for name in seen
    ]
    return {
        "futures": int(head.get("futures", 0)),
        "futures_hit": len({int(r["future"]) for r in liqs}),
        "liquidations": len(liqs),
        "trades": trades_total,
        "pct": pct(len(liqs), trades_total),
        "by_symbol": by_symbol,
        "by_scenario": by_scenario,
    }


# ── 백테스트(E1) ───────────────────────────────────────────────────────────────


def bt_candles(store: Store, symbol: str) -> Candles:
    """종목 하나의 봉 전부.

    Args:
        store: 백테스트 저장소.
        symbol: 종목.

    Returns:
        봉 — 시각은 `.i32`, OHLC 는 `.f32` 에서.
    """
    meta = store.head["candles"][symbol]
    bars = int(meta["bars"])
    ts = store.ints(int(meta["ts_offset"]), bars)
    return _ohlc(store, int(meta["f32_offset"]), bars, ts)


def bt_equity(store: Store) -> tuple[list[int], list[float]]:
    """자본 곡선.

    Args:
        store: 백테스트 저장소.

    Returns:
        `(시각들, 값들)`.
    """
    meta = store.head["equity"]
    bars = int(meta["bars"])
    return store.ints(int(meta["ts_offset"]), bars), store.floats(int(meta["f32_offset"]), bars)


def bt_trades(store: Store, symbol: str | None = None) -> list[dict[str, Any]]:
    """매매 전부(·종목 하나).

    Args:
        store: 백테스트 저장소.
        symbol: 종목. None 이면 전부.

    Returns:
        매매 행들 — 저장된 순서(진입 시각) 그대로.
    """
    rows: list[dict[str, Any]] = store.head.get("trades", [])
    return [dict(r) for r in rows if not symbol or r["symbol"] == symbol]


def write_backtest_store(
    path: Path,
    *,
    head: dict[str, Any],
    candles: dict[str, tuple[list[int], list[tuple[float, float, float, float]]]],
    equity: tuple[list[int], list[float]],
) -> None:
    """백테스트 저장소를 쓴다 — `Store.load` 가 읽는 그 모양 (JSON 머리 + `.f32` + `.i32`).

    견본 매매법(`scripts/build/sample_evidence.py`)이 E1 과 같은 화면(⑤ 차트 절)에 실리려면 같은
    한다 (사용자 2026-09-08 "견본을 골라 그 결과가 나오게").

    Args:
        path: `<이름>.json`. 옆에 `.f32` · `.i32` 를 같이 쓴다.
        head: 요약 열쇠들. `candles` · `equity` · `dtype` 은 여기서 채운다 (있으면 덮는다).
        candles: 종목 → (봉 시작 시각들 · OHLC 들). 종목 순서가 배열 순서다.
        equity: (시각들 · 값들) — 자본 곡선.
    """
    f32: array[float] = array("f")
    i32: array[int] = array("i")
    meta_candles: dict[str, dict[str, int]] = {}
    for symbol, (ts, ohlc) in candles.items():
        meta_candles[symbol] = {"ts_offset": len(i32), "f32_offset": len(f32), "bars": len(ts)}
        i32.extend(ts)
        for o, h, lo, c in ohlc:
            f32.extend((o, h, lo, c))
    eq_ts, eq_values = equity
    meta_equity = {"ts_offset": len(i32), "f32_offset": len(f32), "bars": len(eq_ts)}
    i32.extend(eq_ts)
    f32.extend(eq_values)
    if sys.byteorder != "little":
        f32.byteswap()
        i32.byteswap()
    out = {
        **head,
        "candles": meta_candles,
        "equity": meta_equity,
        "dtype": {"f32": "float32 little-endian", "i32": "int32 little-endian epoch seconds"},
    }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    path.with_suffix(".f32").write_bytes(f32.tobytes())
    path.with_suffix(".i32").write_bytes(i32.tobytes())


def bt_summary(store: Store) -> dict[str, Any]:
    """매매 목록·배열 위치를 뺀 머리 — 목록 화면용.

    Args:
        store: 백테스트 저장소.

    Returns:
        요약 dict.
    """
    skip = {"trades", "candles", "equity", "dtype"}
    return {k: v for k, v in store.head.items() if k not in skip}


# ── 공통 ─────────────────────────────────────────────────────────────────────


def decimate_xy(
    ts: list[int], values: list[float], limit: int = 1500
) -> tuple[list[int], list[float]]:
    """그리기용 표본 축소 — 첫 점·끝 점을 남기고 균등 간격.

    Args:
        ts: 시각들.
        values: 값들.
        limit: 남길 점 수.

    Returns:
        `(시각들, 값들)`. `limit` 이하면 그대로.
    """
    n = len(ts)
    if n <= limit:
        return ts, values
    last = n - 1
    idx = [round(last * k / (limit - 1)) for k in range(limit)]
    return [ts[i] for i in idx], [values[i] for i in idx]
