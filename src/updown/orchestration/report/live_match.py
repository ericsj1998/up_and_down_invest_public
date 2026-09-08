"""라이브 코인 경로는 45미래 중 어느 것을 닮아가나 — 순수 계산 (T222 2단계).

입력은 둘이다: (1) `scenario_paths.py` 가 저장한 45미래의 시장 지수 경로(6종 동일가중 로그 가격 ·
4h), (2) 펀드 시작 이후 라이브 6종의 같은 지수. 같은 길이(지금까지 지난 봉 수 N)의 **앞부분**을 세
가지 모양 지표 — 누적 수익 · 실현 변동성 · 최대 낙폭 — 로 요약하고, 45미래 사이의 표준편차로 나눈
유클리드 거리로 가까운 순서를 매긴다.

🔴 **이것은 예언이 아니다.** T200 의 결론은 "같은 씨앗이 아홉 드리프트 전부에서 1등 = 성과를 정하는
것은 방향이 아니라 경로 모양" 이었다. 닮은 미래의 결말은 그 미래의 결말일 뿐이다. 그래서 (a) 표본이
90일(4h 봉 540개) 미만이면 `sufficient=False` 로 표시하고, (b) 거리는 절대 척도가 아니라 순위로만
읽는다.

⚠️ **런타임 코드는 numpy·pandas 를 들이지 않는다** (`tests/test_indicators.py` 격리 규칙). 그래서
경로는 npz 가 아니라 **원시 float32 + JSON 머리**로 저장되고(`scenario_paths.py`), 여기서는 표준
`array` 로 읽어 순수 파이썬으로 센다. 45 x 10k 봉을 한 번 훑는 데 수백 ms — 30분 캐시 뒤에 있으니
충분하다.

이 모듈은 네트워크를 만지지 않는다 — 배열을 받아 자료형을 돌려준다. 부르는 것은
`apps/api/evidence.py`.
"""

from __future__ import annotations

import json
import math
import sys
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import stdev
from typing import Any, cast

MIN_BARS = 540
"""표본 하한 — 4h 봉 540개 = 90일. 이보다 짧으면 '표본 부족' 으로만 보여 준다."""

BARS_PER_YEAR_4H = 6 * 365

type Row = array[float]
"""float32 한 줄 (`array('f')`). PEP 695 별칭 — 런타임에 평가되지 않아 첨자 문제가 없다."""


@dataclass(frozen=True)
class Paths:
    """저장된 45미래 경로 묶음."""

    scenario: tuple[str, ...]
    seed: tuple[int, ...]
    mu_pct: tuple[float, ...]
    total_pct: tuple[float, ...]
    mdd_pct: tuple[float, ...]
    liquidations: tuple[int, ...]
    market_index: tuple[Row, ...]
    fund_equity: tuple[Row, ...]
    symbols: tuple[str, ...]
    bars_per_year: int
    block: str
    basis: str
    generated: str

    @property
    def count(self) -> int:
        """미래 수 (45)."""
        return len(self.scenario)

    @property
    def length(self) -> int:
        """경로 길이 (4h 봉 수)."""
        return len(self.market_index[0]) if self.market_index else 0


def load_paths(meta_path: Path) -> Paths:
    """`scenario_paths.py` 산출물(JSON 머리 + 옆의 `.f32` 원시 바이트)을 읽는다.

    바이트 배치는 `market_index[futures][bars]` 뒤에 `fund_equity[futures][bars]` · LE float32.

    Args:
        meta_path: JSON 머리 경로.

    Returns:
        45미래 경로 묶음.

    Raises:
        ValueError: 머리와 바이트 길이가 맞지 않으면 — 반쪽 파일을 조용히 쓰지 않는다 (규칙 #8).
    """
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    futures = int(meta["futures"])
    bars = int(meta["bars"])
    raw: Row = array("f")
    raw.frombytes(meta_path.with_suffix(".f32").read_bytes())
    if sys.byteorder == "big":
        raw.byteswap()
    if len(raw) != 2 * futures * bars:
        raise ValueError(f"경로 바이트 길이가 머리와 다르다: {len(raw)} != 2*{futures}*{bars}")

    def rows(offset: int) -> tuple[Row, ...]:
        """원시 배열을 미래별 행으로 자른다.

        Args:
            offset: 블록 시작 위치.

        Returns:
            미래 수만큼의 행.
        """
        return tuple(raw[offset + i * bars : offset + (i + 1) * bars] for i in range(futures))

    return Paths(
        scenario=tuple(str(s) for s in meta["scenario"]),
        seed=tuple(int(s) for s in meta["seed"]),
        mu_pct=tuple(float(s) for s in meta["mu_pct"]),
        total_pct=tuple(float(s) for s in meta["total_pct"]),
        mdd_pct=tuple(float(s) for s in meta["mdd_pct"]),
        liquidations=tuple(int(s) for s in meta["liquidations"]),
        market_index=rows(0),
        fund_equity=rows(futures * bars),
        symbols=tuple(str(s) for s in meta["symbols"]),
        bars_per_year=int(meta["bars_per_year"]),
        block=str(meta["block"]),
        basis=str(meta["basis"]),
        generated=str(meta["generated"]),
    )


def log_index_from_closes(closes: Mapping[str, Sequence[float]]) -> list[float]:
    """종목별 종가열 → 동일가중 로그 가격 지수 (첫 봉 = 0). 길이가 다르면 짧은 쪽에 맞춘다.

    Args:
        closes: 종목 → 종가열.

    Returns:
        로그 지수 (첫 봉 0).

    Raises:
        ValueError: 종목이 없거나 봉이 2개 미만이거나 0 이하 가격이 있으면.
    """
    if not closes:
        raise ValueError("종목이 없다")
    n = min(len(v) for v in closes.values())
    if n < 2:
        raise ValueError("봉이 2개 미만이다")
    acc = [0.0] * n
    for values in closes.values():
        head = values[:n]
        if any(v <= 0 for v in head):
            raise ValueError("0 이하 가격")
        base = math.log(head[0])
        for i, v in enumerate(head):
            acc[i] += math.log(v) - base
    k = len(closes)
    return [v / k for v in acc]


@dataclass(frozen=True)
class Features:
    """경로 모양 세 숫자 — 전부 % · 변동성은 연환산."""

    cum_pct: float
    vol_pct: float
    mdd_pct: float


def features(log_index: Sequence[float], *, bars_per_year: int = BARS_PER_YEAR_4H) -> Features:
    """로그 지수 경로의 누적 수익 · 연환산 실현 변동성 · 최대 낙폭.

    Args:
        log_index: 로그 지수 경로.
        bars_per_year: 연환산 계수 (4h 봉이면 2190).

    Returns:
        세 지표.

    Raises:
        ValueError: 봉이 2개 미만이다.
    """
    n = len(log_index)
    if n < 2:
        raise ValueError("봉이 2개 미만이다")
    x0 = float(log_index[0])
    cum = (math.exp(float(log_index[-1]) - x0) - 1.0) * 100
    rets = [float(log_index[i + 1]) - float(log_index[i]) for i in range(n - 1)]
    vol = stdev(rets) * math.sqrt(bars_per_year) * 100 if len(rets) > 1 else 0.0
    peak = 0.0
    worst = 0.0
    for v in log_index:
        price = math.exp(float(v) - x0)
        peak = max(peak, price)
        worst = max(worst, 1.0 - price / peak)
    return Features(cum_pct=cum, vol_pct=vol, mdd_pct=worst * 100)


@dataclass(frozen=True)
class Match:
    """미래 하나와의 거리와 그 미래의 결말."""

    rank: int
    scenario: str
    seed: int
    mu_pct: float
    distance: float
    head: Features
    outcome_total_pct: float
    outcome_mdd_pct: float
    outcome_liquidations: int


@dataclass(frozen=True)
class MatchResult:
    """비교 결과 — 표본 크기 · 라이브 모양 · 가까운 순서."""

    bars: int
    min_bars: int
    sufficient: bool
    live: Features
    nearest: tuple[Match, ...]
    all: tuple[Match, ...]
    scale: Features
    """거리를 재는 자(45미래 앞부분 세 지표의 표준편차)."""


def _spread(values: Sequence[float]) -> float:
    s = stdev(values) if len(values) > 1 else 0.0
    return s if s > 0 else 1.0


def match(live_index: Sequence[float], paths: Paths, *, top: int = 5) -> MatchResult:
    """라이브 지수(지금까지 N봉)와 45미래의 같은 길이 앞부분을 견준다.

    거리 = 세 지표를 45미래의 표준편차로 나눈 뒤의 유클리드 거리. 표준편차가 0 이면 1 로 둔다.

    Args:
        live_index: 라이브 로그 지수.
        paths: 45미래.
        top: 가까운 순으로 몇 개.

    Returns:
        라이브 지표 · 가장 가까운 미래들 · 거리.

    Raises:
        ValueError: 라이브 봉이 2개 미만.
    """
    n = min(len(live_index), paths.length)
    if n < 2:
        raise ValueError("라이브 봉이 2개 미만이다")
    live_f = features(live_index[:n], bars_per_year=paths.bars_per_year)
    heads = [
        features(paths.market_index[i][:n], bars_per_year=paths.bars_per_year)
        for i in range(paths.count)
    ]
    scale = Features(
        cum_pct=_spread([h.cum_pct for h in heads]),
        vol_pct=_spread([h.vol_pct for h in heads]),
        mdd_pct=_spread([h.mdd_pct for h in heads]),
    )
    dist = [
        math.sqrt(
            ((h.cum_pct - live_f.cum_pct) / scale.cum_pct) ** 2
            + ((h.vol_pct - live_f.vol_pct) / scale.vol_pct) ** 2
            + ((h.mdd_pct - live_f.mdd_pct) / scale.mdd_pct) ** 2
        )
        for h in heads
    ]
    order = sorted(range(paths.count), key=lambda i: (dist[i], i))
    ranked = tuple(
        Match(
            rank=r + 1,
            scenario=paths.scenario[i],
            seed=paths.seed[i],
            mu_pct=paths.mu_pct[i],
            distance=dist[i],
            head=heads[i],
            outcome_total_pct=paths.total_pct[i],
            outcome_mdd_pct=paths.mdd_pct[i],
            outcome_liquidations=paths.liquidations[i],
        )
        for r, i in enumerate(order)
    )
    return MatchResult(
        bars=len(live_index),
        min_bars=MIN_BARS,
        sufficient=len(live_index) >= MIN_BARS,
        live=live_f,
        nearest=ranked[:top],
        all=ranked,
        scale=scale,
    )


def decimate(values: Sequence[float], limit: int = 400) -> list[float]:
    """그리기용으로 점을 줄인다 — 첫 점·끝 점은 남긴다.

    Args:
        values: 값들.
        limit: 남길 점 수.

    Returns:
        float 목록. `limit` 이하면 그대로.
    """
    arr = [float(v) for v in values]
    if len(arr) <= limit:
        return arr
    last = len(arr) - 1
    return [arr[round(last * k / (limit - 1))] for k in range(limit)]


def to_jsonable(obj: Any) -> Any:
    """Dataclass 트리 → JSON 으로 쓸 수 있는 값.

    Args:
        obj: dataclass · dict · list · 스칼라.

    Returns:
        같은 모양의 순수 JSON 값.
    """
    if hasattr(obj, "__dataclass_fields__"):
        data: dict[str, Any] = asdict(obj)
        return {k: to_jsonable(v) for k, v in data.items()}
    if isinstance(obj, dict):
        mapping = cast(dict[Any, Any], obj)
        return {str(k): to_jsonable(v) for k, v in mapping.items()}
    if isinstance(obj, list | tuple | array):
        seq = cast(Sequence[Any], obj)
        return [to_jsonable(v) for v in seq]
    return obj
