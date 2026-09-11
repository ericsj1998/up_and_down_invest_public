"""갈래 설정 · 구조 기반 롱/숏 후보 · 현재가 대비 거리 (T273 · 순수).

후보는 **제안**이다. 손절·익절의 확정은 `decision.risk.manual.confirm` 이 하고, 이 모듈은
"구조가 말하는 자리" 를 숫자로 옮길 뿐이다. 후보를 못 만들면 None 이다 — 지어내지 않는다(규칙 #8).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from updown.analysis.plan import MIN_STOP_PCT
from updown.common.domain.instrument import Timeframe

DEFAULT_BUCKETS_PATH = Path(__file__).resolve().parents[4] / "config" / "analysis_buckets.yml"


class BucketConfigError(ValueError):
    """갈래 설정을 읽을 수 없다 — 기본값으로 넘어가지 않는다 (절대 규칙 #8)."""


@dataclass(frozen=True, slots=True)
class Bucket:
    """매매 갈래 하나 — 시간축과 계획 규칙.

    Attributes:
        key: `short` · `swing` · `long`.
        label: 화면 이름.
        entry: 진입 축.
        context: 맥락 축들.
        valid_bars: 계획이 진입가에 닿아야 하는 봉 수.
        rr: 익절 목표 배수.
        stop_atr: 손절을 구조 뒤로 두는 ATR 배수.
    """

    key: str
    label: str
    entry: Timeframe
    context: tuple[Timeframe, ...]
    valid_bars: int
    rr: Decimal
    stop_atr: Decimal


def load_buckets(path: Path | None = None) -> dict[str, Bucket]:
    """`config/analysis_buckets.yml` → 갈래 표.

    Args:
        path: 설정 파일. 없으면 기본.

    Returns:
        키 → 갈래 (파일 순서).

    Raises:
        BucketConfigError: 파일이 없거나 항목·축 이름이 틀린 경우.
    """
    target = path or DEFAULT_BUCKETS_PATH
    try:
        raw = cast("dict[str, Any]", yaml.safe_load(target.read_text(encoding="utf-8")))
    except OSError as exc:
        raise BucketConfigError(f"갈래 설정을 읽을 수 없다: {target}") from exc
    blocks = cast("dict[str, Any]", raw.get("buckets") or {})
    if not blocks:
        raise BucketConfigError("`buckets` 가 비어 있다")
    out: dict[str, Bucket] = {}
    for key, block in blocks.items():
        try:
            b = cast("dict[str, Any]", block)
            out[str(key)] = Bucket(
                key=str(key),
                label=str(b["label"]),
                entry=Timeframe(str(b["entry"])),
                context=tuple(
                    Timeframe(str(c)) for c in cast("list[object]", b.get("context") or [])
                ),
                valid_bars=int(b["valid_bars"]),
                rr=Decimal(str(b["rr"])),
                stop_atr=Decimal(str(b["stop_atr"])),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BucketConfigError(f"buckets.{key} 가 틀렸다: {exc}") from exc
        if out[str(key)].valid_bars <= 0 or out[str(key)].rr <= 0:
            raise BucketConfigError(f"buckets.{key}: valid_bars·rr 는 0 보다 커야 한다")
    return out


@dataclass(frozen=True, slots=True)
class Limits:
    """AI 비교 상한 (보완 ⑥).

    Attributes:
        runs_per_user_per_day: 사람·하루 최대 회수.
        reuse_minutes: 같은 종목·갈래의 지난 회차를 다시 쓰는 시간(분).
    """

    runs_per_user_per_day: int = 20
    reuse_minutes: int = 10


def load_limits(path: Path | None = None) -> Limits:
    """`config/analysis_buckets.yml` 의 `limits` — 없으면 기본값.

    Args:
        path: 설정 파일.

    Returns:
        상한.

    Raises:
        BucketConfigError: 파일이 없거나 값이 정수가 아닌 경우.
    """
    target = path or DEFAULT_BUCKETS_PATH
    try:
        raw = cast("dict[str, Any]", yaml.safe_load(target.read_text(encoding="utf-8")))
    except OSError as exc:
        raise BucketConfigError(f"갈래 설정을 읽을 수 없다: {target}") from exc
    block = cast("dict[str, Any]", raw.get("limits") or {})
    try:
        return Limits(
            runs_per_user_per_day=int(block.get("runs_per_user_per_day", 20)),
            reuse_minutes=int(block.get("reuse_minutes", 10)),
        )
    except (TypeError, ValueError) as exc:
        raise BucketConfigError(f"limits 가 틀렸다: {exc}") from exc


@dataclass(frozen=True, slots=True)
class Candidate:
    """구조가 말하는 계획 후보 — 확정 전.

    Attributes:
        short: 숏인가.
        entry: 진입가.
        stop: 손절가.
        first: 1차 익절.
        target: 목표.
        basis: 어느 구조에서 나왔나 (사람이 읽는 한 줄).
    """

    short: bool
    entry: Decimal
    stop: Decimal
    first: Decimal
    target: Decimal
    basis: str


def _mid(a: Decimal, b: Decimal) -> Decimal:
    return (a + b) / 2


def tick_of(last: Decimal) -> Decimal:
    """가격을 적을 자릿수 — 현재가가 쓴 소수 자릿수 그대로 (`76987.5` → `0.1` · `648.21` → `0.01`).

    호가 눈금표 없이도 화면이 `78435.72404081035…` 같은 수를 보지 않게 한다(사용자 2026-09-11).

    Args:
        last: 마지막 종가.

    Returns:
        quantize 에 넘길 눈금.
    """
    exponent = last.normalize().as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= 0:
        return Decimal(1)
    return Decimal(1).scaleb(exponent)


def snap(value: Decimal, tick: Decimal, rounding: str = ROUND_HALF_UP) -> Decimal:
    """값을 눈금에 맞춘다 — 기본 반올림. 손절은 진입에서 **먼 쪽**으로(하한을 눈금이 못 깎게).

    Args:
        value: 맞출 값.
        tick: 호가단위.
        rounding: `decimal` 라운딩 모드.

    Returns:
        눈금에 맞춘 값.
    """
    return value.quantize(tick, rounding=rounding)


def candidates_of(
    *,
    last: Decimal,
    atr: Decimal | None,
    support: tuple[Decimal, Decimal] | None,
    resistance: tuple[Decimal, Decimal] | None,
    rr: Decimal,
    stop_atr: Decimal,
    short_allowed: bool,
) -> dict[str, Candidate | None]:
    """현재가 · ATR · 아래 첫 지지(low, high) · 위 첫 저항(low, high) → 롱/숏 후보.

    롱은 지지 상단에서 사고 지지 하단 - ATR x `stop_atr` 에 손절, 목표는 **첫 저항 하단과 손절 거리
    x rr 중 가까운 쪽**(결정 2026-09-11 권장안 "둘 다 계산해 좁은 쪽"). 숏은 거울. 지지가 없으면
    롱 후보 없음, 저항이 없으면 숏 후보 없음 — 구조 없이 숫자를 만들지 않는다.

    Args:
        last: 마지막 종가.
        atr: ATR(14). 없으면 손절 여유를 0 으로.
        support: 현재가 아래 첫 지지 띠 (low, high).
        resistance: 현재가 위 첫 저항 띠 (low, high).
        rr: 익절 목표 배수.
        stop_atr: 손절 여유 ATR 배수.
        short_allowed: 능력표 — 거짓이면 숏 후보는 None.

    Returns:
        `{"long": Candidate | None, "short": Candidate | None}`.
    """
    pad = (atr or Decimal(0)) * stop_atr
    tick = tick_of(last)
    out: dict[str, Candidate | None] = {"long": None, "short": None}
    if support is not None and support[1] < last:
        entry = support[1]
        stop = support[0] - pad
        # ⭐ 손절폭 하한 0.5% (T173 · `MIN_STOP_PCT`). 띠가 좁고 ATR 이 작으면
        #    구조 손절이 0.16% 처럼 나와 노이즈에 먼저 죽고 필요 승률이 96% 가 된다
        #    (실계좌 BTC 1h 실측 2026-09-11) — 하한까지 넓힌다. 눈금은 진입에서
        #    먼 쪽으로 붙여 하한이 반올림에 깎이지 않게 한다.
        floored = entry * (1 - MIN_STOP_PCT)
        widened = stop > floored
        stop = min(stop, floored)
        if stop < entry:
            by_rr = entry + (entry - stop) * rr
            target = (
                min(by_rr, resistance[0])
                if resistance is not None and resistance[0] > entry
                else by_rr
            )
            if target > entry:
                out["long"] = Candidate(
                    short=False,
                    entry=snap(entry, tick),
                    stop=snap(stop, tick, ROUND_FLOOR),
                    first=snap(_mid(entry, target), tick),
                    target=snap(target, tick),
                    basis=f"지지 {support[0]}~{support[1]} 상단 진입 · 지지 하단 -ATR x{stop_atr} "
                    + "손절 · "
                    + ("첫 저항" if target != by_rr else f"손절 거리 x{rr}")
                    + (f" · 손절폭 하한 {MIN_STOP_PCT * 100:.1f}% 로 넓힘" if widened else ""),
                )
    if short_allowed and resistance is not None and resistance[0] > last:
        entry = resistance[0]
        stop = resistance[1] + pad
        ceiled = entry * (1 + MIN_STOP_PCT)
        widened = stop < ceiled
        stop = max(stop, ceiled)
        if stop > entry:
            by_rr = entry - (stop - entry) * rr
            target = max(by_rr, support[1]) if support is not None and support[1] < entry else by_rr
            if 0 < target < entry:
                out["short"] = Candidate(
                    short=True,
                    entry=snap(entry, tick),
                    stop=snap(stop, tick, ROUND_CEILING),
                    first=snap(_mid(entry, target), tick),
                    target=snap(target, tick),
                    basis=f"저항 {resistance[0]}~{resistance[1]} 하단 진입 · 저항 상단 +ATR "
                    + f"x{stop_atr} 손절 · "
                    + ("첫 지지" if target != by_rr else f"손절 거리 x{rr}")
                    + (f" · 손절폭 하한 {MIN_STOP_PCT * 100:.1f}% 로 넓힘" if widened else ""),
                )
    return out


@dataclass(frozen=True, slots=True)
class Distances:
    """현재가 대비 거리 — 화면이 "진입가까지 -1.2% · 손절 -3.1% · 목표 +4.8%" 로 읽는다.

    Attributes:
        to_entry_pct: (진입 - 현재) / 현재 x 100.
        to_stop_pct: (손절 - 현재) / 현재 x 100.
        to_target_pct: (목표 - 현재) / 현재 x 100.
        risk_pct: |진입 - 손절| / 진입 x 100.
        reward_pct: |목표 - 진입| / 진입 x 100.
        rr: reward / risk.
    """

    to_entry_pct: Decimal
    to_stop_pct: Decimal
    to_target_pct: Decimal
    risk_pct: Decimal
    reward_pct: Decimal
    rr: Decimal


def _pct(a: Decimal, base: Decimal) -> Decimal:
    return ((a - base) / base * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def distances_of(last: Decimal, entry: Decimal, stop: Decimal, target: Decimal) -> Distances:
    """거리 여섯 — 분모는 현재가(진입까지·손절·목표) 와 진입가(리스크·보상).

    Args:
        last: 현재가.
        entry: 진입가.
        stop: 손절가.
        target: 목표가.

    Returns:
        거리.

    Raises:
        ValueError: 현재가·진입가가 0 이하이거나 손절이 진입가와 같은 경우.
    """
    if last <= 0 or entry <= 0:
        raise ValueError("현재가·진입가는 0 보다 커야 한다")
    risk = abs(entry - stop)
    if risk == 0:
        raise ValueError("손절이 진입가와 같다")
    reward = abs(target - entry)
    return Distances(
        to_entry_pct=_pct(entry, last),
        to_stop_pct=_pct(stop, last),
        to_target_pct=_pct(target, last),
        risk_pct=(risk / entry * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        reward_pct=(reward / entry * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        rr=(reward / risk).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


__all__ = [
    "Bucket",
    "BucketConfigError",
    "Candidate",
    "Distances",
    "candidates_of",
    "distances_of",
    "load_buckets",
    "snap",
    "tick_of",
]
