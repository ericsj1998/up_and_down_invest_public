"""첫 화면 시장 칸 — 주요 종목 시세 · 거시 지표 · 매매법이 지금 읽는 문 값 (사용자 2026-11-21).

*"BTC · ETH · 공포지수 같은 것들을 간이 홈에서 확인하고 싶어 · 매매법 근거에 쓰인 종목 · 지수도"*.

**새로 계산하지 않는다.** 문 값은 실계좌 판(세션)이 이미 넣어 둔 값을 읽고, 문턱 · 다리 이름은 판이
들고 있는 선언에서 읽는다(이 모듈에 매매법 값을 적지 않는다). 시세는 거래소 공개 티커 한 번 · 거시
지표는 거시 캐시 · VIX 공식 종가는 한 시간 캐시. 화면은 칸을 열 때만 묻는다(서버 60 초 묶음).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

OPEN = "열림"
REST = "쉼"
UNKNOWN = "모름"


@dataclass(frozen=True, slots=True)
class GateRow:
    """문 한 줄.

    Attributes:
        key: 문 이름(선언 칸).
        name: 화면 이름.
        legs: 그 문을 선언한 다리들(선언의 짧은 이름).
        value: 지금 값(사람이 읽는 꼴).
        rule: 문턱(사람이 읽는 꼴).
        state: `열림` · `쉼` · `모름`(모름 = 그 다리 새 진입 보류 · 규칙 #8-1).
        note: 덧말.
    """

    key: str
    name: str
    legs: tuple[str, ...]
    value: str
    rule: str
    state: str
    note: str = ""

    def as_json(self) -> dict[str, Any]:
        """화면용."""
        return {
            "key": self.key,
            "name": self.name,
            "legs": list(self.legs),
            "value": self.value,
            "rule": self.rule,
            "state": self.state,
            "note": self.note,
        }


def _pct(value: Decimal | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value * 100:+.{digits}f}%"


def _leg_name(book: Any) -> str:
    return str(getattr(book, "short_label", None) or getattr(book, "playbook_id", "?"))


def _declared(sessions: Iterable[Any], field: str) -> tuple[Any, tuple[str, ...], list[Any]]:
    """선언한 첫 다리의 규칙 · 선언한 다리 이름들 · 그 다리를 실은 세션들."""
    rule = None
    legs: list[str] = []
    holders: list[Any] = []
    for s in sessions:
        mine = [b for b in getattr(s, "playbooks", ()) if getattr(b, field, None) is not None]
        if not mine:
            continue
        holders.append(s)
        for b in mine:
            rule = rule if rule is not None else getattr(b, field)
            name = _leg_name(b)
            if name not in legs:
                legs.append(name)
    return rule, tuple(legs), holders


def _value(holders: Sequence[Any], attr: str) -> Any:
    """그 값을 가진 첫 세션의 값 — 다 None 이면 None(모름)."""
    return next((v for s in holders if (v := getattr(s, attr, None)) is not None), None)


def next_release(schedule: Any, now: datetime, kinds: Sequence[str]) -> tuple[str, datetime] | None:
    """`now` 뒤 가장 가까운 발표(종류 · 시각) — 일정이 없으면 None."""
    times: Mapping[str, Sequence[datetime]] = getattr(schedule, "times", {}) or {}
    best: tuple[str, datetime] | None = None
    for kind in kinds:
        for at in times.get(kind, ()):
            if at > now and (best is None or at < best[1]):
                best = (kind, at)
                break
    return best


def gate_rows(sessions: Sequence[Any], now: datetime) -> list[GateRow]:
    """실계좌 판들이 들고 있는 문 값 · 선언 → 화면 줄들(순수 · 선언한 문만).

    Args:
        sessions: 도는 실계좌 세션들(`playbooks` · `ref_*` · `market_*` · `release_schedule` 칸).
        now: 지금(UTC).

    Returns:
        문마다 한 줄(선언한 다리가 없으면 그 문은 빠진다).
    """
    rows: list[GateRow] = []
    rule, legs, holders = _declared(sessions, "entry_ref_return_band")
    if rule is not None:
        v = _value(holders, "ref_return")
        rows.append(
            GateRow(
                "entry_ref_return_band",
                f"BTC {int(rule.bars) // 6} 일 수익",
                legs,
                _pct(v),
                f"{_pct(rule.low, 0)} ~ {_pct(rule.high, 0)} 안이면 듦",
                UNKNOWN if v is None else (OPEN if rule.holds(v) else REST),
            )
        )
    rule, legs, holders = _declared(sessions, "entry_ref_surge_cap")
    if rule is not None:
        v = _value(holders, "ref_surge")
        rows.append(
            GateRow(
                "entry_ref_surge_cap",
                f"BTC {rule.days} 일 급등",
                legs,
                _pct(v),
                f"{_pct(rule.high)} 넘으면 쉼",
                UNKNOWN if v is None else (OPEN if rule.holds(v) else REST),
            )
        )
    rule, legs, holders = _declared(sessions, "entry_ref_vol_off")
    if rule is not None:
        v = _value(holders, "ref_vol_sd_pct")
        rows.append(
            GateRow(
                "entry_ref_vol_off",
                f"BTC {rule.days} 일 변동",
                legs,
                "—" if v is None else f"{v:.2f}%",
                f"{rule.min_pct}% 이상이면 쉼",
                UNKNOWN if v is None else (REST if rule.blocks(v) else OPEN),
            )
        )
    rule, legs, holders = _declared(sessions, "entry_ref_vol_rank")
    if rule is not None:
        v = _value(holders, "ref_vol_rank")
        rows.append(
            GateRow(
                "entry_ref_vol_rank",
                f"BTC {rule.days} 일 변동 순위({rule.window} 일 중)",
                legs,
                "—" if v is None else f"{v:.0f}",
                f"{rule.min_rank} 이상이면 쉼",
                UNKNOWN if v is None else (REST if rule.blocks(v) else OPEN),
            )
        )
    rule, legs, holders = _declared(sessions, "entry_washed")
    if rule is not None:
        ref = _value(holders, "ref_washed_ret")
        breadth = _value(holders, "market_breadth")
        lag = _value(holders, "market_lag_ret")
        if ref is None or breadth is None or lag is None:
            state, note = UNKNOWN, ""
        elif ref >= 0:
            state, note = OPEN, f"BTC {rule.ref_days} 일 + 라 안 봄"
        else:
            hits = int(breadth < rule.breadth_below) + int(lag < 0)
            if hits >= rule.need:
                state, note = REST, "시장 조건만으로 쉼"
            elif hits + 1 >= rule.need:
                state, note = OPEN, "종목 직전 펀딩이 마이너스인 종목은 쉼"
            else:
                state, note = OPEN, ""
        rows.append(
            GateRow(
                "entry_washed",
                "씻긴 시장",
                legs,
                f"BTC {rule.ref_days} 일 {_pct(ref)} · 종목 폭 "
                + ("—" if breadth is None else f"{breadth * 100:.0f}%")
                + f" · 뒤처진 묶음 {rule.lag_days} 일 {_pct(lag)}",
                f"BTC 마이너스이고 (폭 < {rule.breadth_below * 100:.0f}% · 뒤처진 묶음 마이너스 · "
                f"종목 펀딩 마이너스) "
                f"{rule.need} 개 이상이면 쉼",
                state,
                note,
            )
        )
    rule, legs, holders = _declared(sessions, "entry_event_window")
    if rule is not None:
        schedule = _value(holders, "release_schedule")
        if schedule is None:
            rows.append(
                GateRow(
                    "entry_event_window", "미국 발표", legs, "—", "", UNKNOWN, "일정 모름 = 보류"
                )
            )
        else:
            inside = schedule.inside(now, rule.kinds, rule.hours)
            nxt = next_release(schedule, now, rule.kinds)
            value = "—" if nxt is None else f"다음 {nxt[0]} {nxt[1].isoformat()}"
            rows.append(
                GateRow(
                    "entry_event_window",
                    "미국 발표",
                    legs,
                    value,
                    f"발표 ±{rule.hours} 시간 쉼",
                    UNKNOWN if inside is None else (REST if inside else OPEN),
                )
            )
    return rows


def fear_row(
    engine_status: Mapping[str, Any] | None,
    threshold: float | None,
    closes: Mapping[date, Decimal] | None,
    legs: tuple[str, ...] = (),
) -> GateRow | None:
    """나스닥 공포 조각 한 줄 — 마지막 VIX 공식 종가 대 문턱 · 든 조각 수.

    Args:
        engine_status: 엔진 요약(`fear` 칸 = 든 조각 수).
        threshold: 엔진 선언의 문턱(0 · None 이면 꺼짐 → 줄 없음).
        closes: VIX 공식 종가(날짜 → 값 · 못 받았으면 None).
        legs: 다리 이름.

    Returns:
        줄 하나 · 꺼졌으면 None.
    """
    if not threshold:
        return None
    held = None if engine_status is None else engine_status.get("fear")
    if not closes:
        return GateRow(
            "fear_vix_at",
            "VIX 공식 종가",
            legs,
            "—",
            f"{threshold:g} 이상이면 다음 뉴욕 마감에 조각",
            UNKNOWN,
            "VIX 모름 = 새 조각 쉼",
        )
    day = max(closes)
    v = closes[day]
    armed = float(v) >= threshold
    return GateRow(
        "fear_vix_at",
        "VIX 공식 종가",
        legs,
        f"{v:.2f}({day.isoformat()})",
        f"{threshold:g} 이상이면 다음 뉴욕 마감에 조각",
        OPEN if armed else REST,
        ("다음 마감에 조각 하나" if armed else "조각 안 삼")
        + ("" if held is None else f" · 든 조각 {held}"),
    )


def price_rows(
    tickers: Iterable[Mapping[str, Any]], symbols: Sequence[str]
) -> list[dict[str, str]]:
    """거래소 공개 티커 → 고른 종목의 시세 · 24 시간 등락(%)."""
    by = {str(t.get("contract")): t for t in tickers}
    out: list[dict[str, str]] = []
    for sym in symbols:
        t = by.get(sym)
        if t is None:
            continue
        out.append(
            {
                "symbol": sym,
                "last": str(t.get("last") or ""),
                "change_pct": str(t.get("change_percentage") or ""),
            }
        )
    return out


VIX_TTL = timedelta(hours=1)
"""VIX 공식 종가 캐시 — 하루 한 값이라 한 시간이면 넉넉하다(화면 칸만 · 엔진은 마감마다 따로)."""

__all__ = ["VIX_TTL", "GateRow", "fear_row", "gate_rows", "next_release", "price_rows"]
