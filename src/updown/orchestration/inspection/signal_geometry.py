"""신호 때 탐지기가 본 선 — 일봉 채널 · 삼각수렴 매매를 화면에 그대로 (T378 · 2026-10-05).

## 왜 따로 두나

사용자 2026-10-05: *"화면에 탐지기가 신호 때 본 선을 그대로 그리기 — 이거는 해줬으면 좋겠고."*
화면의 '채널'(`structures/leg_channel.py` · `inspection/snapshot.py`)은 탐지기와
**다른 계산**이다 — 종가 회귀 + 몸통 잔차 최대 · 최소 외곽선 · 최신 봉까지 포함 ·
새로고침마다 다시 그린다. 그래서 돌파봉까지 감싼 "완성된 채널" 처럼 사후에 보이고,
*"채널이 완성됐을 때 들어갔나"* 를 눈으로 확인할 수 없었다. 삼각형은 아예 안 그렸다.

## 무엇을 하나

매매 기록(`TradeRecord`)마다 **그 진입 시점에 이미 닫혀 있던 마지막 판정 봉**(= 신호봉)을
찾고, 그 봉까지의 닫힌 봉 창(세션 `frame_window` 만큼)으로 **탐지기의 같은 함수**
(`private_strategy.channel_lines` · `private_strategy.closed_private_strategy`)를 다시 부른다 —
화면용 근사가 아니다(절대 규칙 #9 · 같은 수식).

## 🔴 여기 들어올 수 없는 것

판단 · 집행. 이 모듈은 이미 끝난 진입을 **설명**만 한다 — 결과가 원장 · 주문에 안 들어간다.

⚠️ 신호봉 시각은 원장에 따로 없다(체결 봉 = 걸음 축 5분봉 · 1.33.1 은 그 봉이 5분 낡았다 ·
T372). 그래서 "체결 봉 시각 + 걸음 한 칸 + 5분 여유" 까지 닫힌 마지막 판정 봉을 신호봉으로 본다.
⚠️ 창 앞머리가 그때와 다르면(라이브 급전이 옛 봉을 버림) Wilder ATR 이 아주 조금 달라
유효 조건 경계에서 다시 잰 사건이 달라질 수 있다 — 그러면 `matched` 가 거짓이고 화면이
그 사실을 적는다(조용히 그리지 않는다).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from updown.analysis.detectors import private_strategy as dcb
from updown.analysis.detectors import private_strategy as tri
from updown.analysis.detectors.rules import load_rules
from updown.analysis.indicators.atr import atr
from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from updown.analysis.detectors.base import RuleParams
    from updown.analysis.detectors.rules import RuleConfig
    from updown.common.domain.candle import Candle
    from updown.orchestration.walkforward.ledger import TradeRecord
    from updown.orchestration.walkforward.session import Session

GEOMETRY_RULES: dict[str, str] = {
    dcb.RULE_ID: "channel",
    dcb.RULE_ID_V8: "channel",
    tri.RULE_ID: "triangle",
}
"""선을 그릴 수 있는 룰 → 모양. 여기 없는 룰의 매매는 건너뛴다."""

MAX_CLOSED = 6
"""닫힌 매매는 최근 이만큼만 — 열린 매매는 전부."""

DECISION_SLACK = timedelta(minutes=5)
"""체결 봉 시각에 더하는 여유 — 1.33.1 의 체결 봉은 5분 낡았다(T372)."""

_CACHE: dict[tuple[str, str, str], dict[str, Any] | None] = {}
"""(종목, 매매 id, 룰) → 그린 값. 끝난 진입의 선은 바뀌지 않으므로 한 번만 잰다."""

_RULES: dict[str, RuleConfig] = {}


def _params(rule_id: str) -> RuleParams | None:
    """룰 설정(`config/rules/<id>.yml`) — 탐지기가 받은 그 값."""
    if not _RULES:
        _RULES.update(load_rules())
    found = _RULES.get(rule_id)
    return None if found is None else found.to_rule_params()


def signal_index(rows: Sequence[Candle], decided_at: datetime, frame: Timeframe) -> int | None:
    """`decided_at` 까지 닫힌 마지막 판정 봉의 위치 (순수).

    Args:
        rows: 판정 봉(오래된 것 → 최신 · 닫힌 봉만).
        decided_at: 진입을 판정한 시각.
        frame: 판정 축.

    Returns:
        위치. 그때 닫힌 봉이 창에 없으면 None.
    """
    span = interval(frame)
    for k in range(len(rows) - 1, -1, -1):
        if rows[k].ts + span <= decided_at:
            return k
    return None


def channel_shape(window: Sequence[Candle], params: RuleParams) -> dict[str, Any] | None:
    """창 마지막 봉에서 일봉 채널 탐지기가 본 선 (순수).

    Args:
        window: 판정 봉(마지막 = 신호봉).
        params: 룰 값.

    Returns:
        `lines` · `note` · `matched`(다시 잰 사건이 돌파 ↑ 인가). 채널이 없으면 None.
    """
    t = len(window) - 1
    if t < max(dcb.WINDOWS) + 20:
        return None
    spans = atr([c.high for c in window], [c.low for c in window], [c.close for c in window])
    breakout = float(params.as_decimal("breakout", "0.075"))
    found = dcb.channel_lines(
        window,
        spans,
        t,
        zone_in=float(params.as_decimal("zone_in", "0.15")),
        zone_out=float(params.as_decimal("zone_out", "0.075")),
        breakout=breakout,
    )
    if found is None:
        return None
    t1, t2 = window[found.start].ts.isoformat(), window[found.end].ts.isoformat()
    return {
        "lines": [
            {"role": "upper", "t1": t1, "p1": found.upper[0], "t2": t2, "p2": found.upper[1]},
            {"role": "lower", "t1": t1, "p1": found.lower[0], "t2": t2, "p2": found.lower[1]},
        ],
        "points": [{"t": t2, "p": found.trigger, "kind": "trigger"}],
        "note": (
            f"일봉 채널 {found.end - found.start}일 · 번갈아 닿기 {found.channel.alt} · "
            f"안쪽 {found.channel.inside:.0%} · 돌파 문턱 {found.trigger:,.4f}"
        ),
        "matched": found.event == "brk_up",
    }


def triangle_shape(window: Sequence[Candle], params: RuleParams) -> dict[str, Any] | None:
    """창 마지막 봉에서 삼각수렴 탐지기가 본 두 변 (순수).

    Args:
        window: 4H 판정 봉(마지막 = 이탈 봉).
        params: 룰 값.

    Returns:
        `lines` · `points`(스윙) · `note` · `matched`(선언한 방향의 이탈인가). 삼각형이 없으면 None.
    """
    found = tri.closed_private_strategy(
        window,
        swing_k=params.as_int("swing_k", 3),
        look=params.as_int("look", 120),
        touch_atr=params.as_decimal("touch_atr", "0.3"),
        min_width_atr=params.as_decimal("min_width_atr", "2"),
        break_atr=params.as_decimal("break_atr", "0.25"),
        where_min=params.as_decimal("where_min", "0.40"),
        where_max=params.as_decimal("where_max", "0.85"),
    )
    if found is None:
        return None
    t = len(window) - 1
    (mu, bu), (ml, bl) = found.upper_line, found.lower_line
    t1, t2 = window[found.first].ts.isoformat(), window[t].ts.isoformat()
    sides = params.as_int("sides", -1)
    return {
        "lines": [
            {"role": "upper", "t1": t1, "p1": mu * found.first + bu, "t2": t2, "p2": mu * t + bu},
            {"role": "lower", "t1": t1, "p1": ml * found.first + bl, "t2": t2, "p2": ml * t + bl},
        ],
        "points": [
            {"t": window[i].ts.isoformat(), "p": p, "kind": kind} for i, kind, p in found.points
        ],
        "note": (
            f"삼각수렴 스윙 {found.legs + 1}개 · 꼭지점까지 "
            f"{(t - found.first) / (found.apex - found.first):.0%} 지점에서 "
            f"{'하방' if found.side < 0 else '상방'} 이탈"
        ),
        "matched": sides == 0 or found.side == sides,
    }


def _shape(session: Session, record: TradeRecord, rule_id: str) -> dict[str, Any] | None:
    params = _params(rule_id)
    if params is None:
        return None
    kind = GEOMETRY_RULES[rule_id]
    frame = (
        Timeframe(str(params.values.get("base_timeframe", dcb.BASE_TIMEFRAME.value)))
        if kind == "channel"
        else tri.BASE_TIMEFRAME
    )
    if frame not in session.feed.timeframes:
        return None
    rows = list(session.feed.judged(frame))
    tick = session.price_frame or session.playbook.timeframe
    k = signal_index(rows, record.placed_at + interval(tick) + DECISION_SLACK, frame)
    if k is None:
        return None
    window = rows[: k + 1]
    if session.frame_window > 0 and len(window) > session.frame_window:
        window = window[-session.frame_window :]
    drawn = channel_shape(window, params) if kind == "channel" else triangle_shape(window, params)
    if drawn is None:
        return None
    return {
        "trade_id": record.trade_id,
        "playbook": record.playbook,
        "kind": kind,
        "frame": frame.value,
        "signal_ts": window[-1].ts.isoformat(),
        **drawn,
    }


def signal_geometry(session: Session, records: Sequence[TradeRecord]) -> list[dict[str, Any]]:
    """열린 매매 전부 + 최근 닫힌 매매의 신호 때 선.

    Args:
        session: 세션(판정 봉 · 플레이북 · 걸음 축).
        records: 원장 매매 기록(오래된 것 → 최신).

    Returns:
        매매마다 `{trade_id, playbook, kind, frame, signal_ts, lines, points, note, matched, open}`.
        선을 그릴 수 없는 매매(룰이 다름 · 신호봉이 창 밖 · 다시 잰 모양이 없음)는 빠진다.
    """
    books = {item.attribution: item for item in session.playbooks}
    opened = [r for r in records if r.opened_at is not None and r.closed_at is None]
    closed = [r for r in records if r.closed_at is not None][-MAX_CLOSED:]
    out: list[dict[str, Any]] = []
    for record in (*closed, *opened):
        book = books.get(record.playbook)
        if book is None:
            continue
        rule_id = next((r for r in book.setups if r in GEOMETRY_RULES), None)
        if rule_id is None:
            continue
        key = (session.instrument.symbol, record.trade_id, rule_id)
        if key not in _CACHE:
            _CACHE[key] = _shape(session, record, rule_id)
        drawn = _CACHE[key]
        if drawn is not None:
            out.append({**drawn, "open": record.closed_at is None})
    return out
