"""열린 매매 하나의 예측 그림 값 — 띠 · 꼬리선 · 부채꼴 · 갈래를 **가격과 시각**으로 (표시 전용).

`forecast.py` 가 R 로 낸 값을, 화면이 바로 긋도록 가격 · 시각으로 바꾸고 그 근거(기준 시각 ·
보유 시간 · 처음 손절 · 연구 검증 간격인가)를 함께 싣는다. 판단 로직은 없다 — 조립만 한다.
T820 · T825.

Note:
    🔴 주문 · 청산에 안 쓴다(절대 규칙 #2 · #4).
    처음 손절을 모르는 매매(이번 배포 전에 연 매매 · 이어받은 포지션)는 **지금 손절을 처음
    손절로 친다**(사용자 2026-11-11). 손절이 본전 이상이면 1R 이 성립하지 않아 그리지 않는다.
    그렇게 그린 것은 `stop_source="assumed"`(이어받은 포지션은 `"adopted"`)로 표시한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from updown.orchestration.inspection.forecast import (
    HOUR_MS,
    STEP_H,
    Bar,
    Forecaster,
    ForecastError,
    btc_regime,
    inputs_from_bars,
    price_of,
)
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

DAY_MS = 24 * HOUR_MS
BRANCH_NAMES = ("불리", "옆", "유리")
"""연구 갈래(내림 · 옆 · 오름)는 R(그 매매에 유리한 쪽이 위) 기준이다.

숏에서 "오름" 은 가격이 내리는 쪽이라, 화면에는 가격 방향이 아닌 유리 · 불리로 적는다."""


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def entry_hour_ms(opened_at: datetime) -> int:
    """진입 시각을 1H 경계로 올린다.

    라이브 `opened_at` 은 걸음 봉 시작(5 분 봉)이라 그 봉이 닫힌 시각이 진입이다.
    예: 15:55 에 연 매매는 16:00 진입 · 진입 기준가 = 15:00 ~ 16:00 봉 종가(연구 정의).
    """
    ms = _ms(opened_at)
    return -(-ms // HOUR_MS) * HOUR_MS


def day_closes(bars_4h: Sequence[Bar]) -> list[tuple[int, float]]:
    """4H 봉 → (UTC 날 시작 ms, 그날 마지막 4H 종가) — 봉이 있는 날만."""
    out: dict[int, float] = {}
    for b in sorted(bars_4h, key=lambda x: x.ts_ms):
        out[b.ts_ms // DAY_MS * DAY_MS] = b.c
    return sorted(out.items())


def trade_forecast(
    record: TradeRecord,
    *,
    label: str,
    model: Forecaster,
    bars: Sequence[Bar],
    btc_days: Sequence[tuple[int, float]] | None,
    now: bool,
) -> dict[str, Any]:
    """열린 매매 하나 → 그림 값(또는 못 그리는 까닭).

    Args:
        record: 원장의 열린 매매.
        label: 그 매매의 다리 짧은 이름(모형 값의 열쇠).
        model: 모형 값.
        bars: 그 종목의 닫힌 1H 봉(시각 오름차순).
        btc_days: BTC 날 종가(`day_closes`). None 이면 BTC 국면 모름(0.5).
        now: 참이면 마지막으로 닫힌 1H 봉 기준 · 거짓이면 연구가 잰 4 시간 간격의 마지막 시점.

    Returns:
        `{trade_id, leg, ok, reason?, basis?, band?, fan?, branch?}`.
    """
    head: dict[str, Any] = {"trade_id": record.trade_id, "leg": label, "ok": False}
    leg = model.legs.get(label)
    if leg is None:
        return head | {"reason": "이 다리는 예측 모형이 없다"}
    if record.outcome is not Outcome.OPEN or record.opened_at is None:
        return head | {"reason": "보유 중이 아니다"}
    sign = 1 if record.direction is Direction.LONG else -1
    if sign != leg.sign:
        return head | {"reason": "모형 방향과 매매 방향이 다르다"}
    entry = float(record.entry)
    stop_source = "initial"
    stop = record.initial_stop
    if stop is None:
        stop_source = "adopted" if record.actor is Actor.ADOPTED else "assumed"
        stop = record.planned_stop
    stop_f = float(stop)
    if entry <= 0 or stop_f <= 0 or (entry - stop_f) * sign <= 0:
        return head | {"reason": "손절이 본전 이상이라 1R 이 성립하지 않는다"}
    stop_pct = abs(entry - stop_f) / entry * 100
    if not bars:
        return head | {"reason": "1 시간봉이 없다"}
    tin = entry_hour_ms(record.opened_at)
    last_end = bars[-1].ts_ms + HOUR_MS
    if last_end < tin:
        return head | {"reason": "진입 봉이 아직 안 닫혔다"}
    step = STEP_H * HOUR_MS
    obs = last_end if now else tin + (last_end - tin) // step * step
    btc = 0.5 if btc_days is None else btc_regime(btc_days, tin // DAY_MS * DAY_MS)
    x = inputs_from_bars(
        bars,
        entry_ms=tin,
        obs_ms=obs,
        sign=sign,
        stop_pct=stop_pct,
        btc_up=btc,
        atr_n=model.atr_n,
        look=model.look,
        want_tree=leg.wants_tree,
    )
    if x is None:
        return head | {"reason": "진입 봉이 서버 봉 창 밖이다"}
    e = bars[next(i for i, b in enumerate(bars) if b.ts_ms == tin - HOUR_MS)].c
    try:
        band_r = leg.band_r(x)
        fan_r = leg.fan_r(x)
        probs = leg.branch_p(x)
    except ForecastError as exc:
        return head | {"reason": f"모형 계산 실패: {exc}"}

    def px(r: float) -> float:
        return price_of(e, sign, stop_pct, r)

    band = leg.band
    tail = bool(band.get("tail"))
    out: dict[str, Any] = head | {
        "ok": True,
        "basis": {
            "at": _iso(obs),
            "hold_h": x.hold_h,
            "on_grid": (obs - tin) % step == 0,
            "cur_r": x.cur_r,
            "entry_ref": e,
            "stop_pct": stop_pct,
            "stop_source": stop_source,
            "btc_known": btc_days is not None,
        },
        "band": {
            "q25": px(band_r[0]),
            "q50": px(band_r[1]),
            "q75": px(band_r[2]),
            "q90": px(band_r[3]) if tail and len(band_r) > 3 else None,
            "note": band.get("note"),
        },
    }
    fan = leg.fan
    origin = px(x.cur_r)
    out["fan"] = {
        "origin": {"at": _iso(obs), "price": origin},
        "points": [
            {
                "at": _iso(obs + int(h) * HOUR_MS),
                "p": [px(x.cur_r + fan_r[qi][jj]) for qi in range(len(fan["qs"]))],
            }
            for jj, h in enumerate(fan["steps_h"])
        ],
        "qs": list(fan["qs"]),
    }
    br = leg.branch
    if probs is not None and br is not None:
        lines: list[dict[str, Any]] = []
        for b, centers in enumerate(br["centers"]):
            if centers is None:
                continue
            pts = [{"at": _iso(obs), "price": origin}] + [
                {"at": _iso(obs + (s + 1) * step), "price": px(x.cur_r + v)}
                for s, v in enumerate(centers)
            ]
            lines.append({"name": BRANCH_NAMES[b], "p": probs[b], "points": pts})
        out["branch"] = {"p": probs, "lines": lines}
    return out


def session_forecasts(
    records: Sequence[TradeRecord],
    *,
    label_of: Callable[[str], str],
    model: Forecaster,
    bars: Sequence[Bar],
    btc_days: Sequence[tuple[int, float]] | None,
    now: bool,
) -> list[dict[str, Any]]:
    """판의 열린 매매 전부."""
    return [
        trade_forecast(
            r,
            label=label_of(r.playbook),
            model=model,
            bars=bars,
            btc_days=btc_days,
            now=now,
        )
        for r in records
        if r.outcome is Outcome.OPEN and r.opened_at is not None
    ]
