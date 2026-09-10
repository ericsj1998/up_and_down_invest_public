"""AI 차트 주문의 참가자 셋 (T273 2단계 · 순수).

- **우리-구조**: `plans.candidates_of` 의 후보 → 실험 원장의 `Proposal`(지정가 · TOUCH). 채점은
  실험 엔진이 한다.
- **AI 단독**: 기존 `/ai/analyze` 와 같은 프롬프트(봉·지표만).
- **AI+근거**: 같은 프롬프트에 우리가 읽은 구조·재무·거시를 "참고" 절로 붙인다 —
  `evidence_note_of` 가 그 글이다.

AI 참가자의 계획은 `LlmProposal` — 표시·기록·채점 전용(§5.3.1). 주문이 되는 값은 RiskManager 가
확정한
우리 계획뿐이고, 그것도 사람이 주문 창에서 보낸다.
"""

from __future__ import annotations

from typing import Any, cast

from updown.common.domain.instrument import Timeframe
from updown.common.domain.setup import EntryTrigger
from updown.marketdata.ingest.timeframes import interval_seconds
from updown.orchestration.ai_experiment.record import ParticipantKind, Proposal, Stance
from updown.orchestration.chart_order.plans import Bucket, Candidate

STRUCTURE_NAME = "우리-구조"
"""우리 참가자 이름 — 탐지기 묶음(`우리-알고리즘`)과 구별한다."""
EVIDENCE_SUFFIX = "+근거"
"""AI+근거 참가자 이름 = 모델 id + 이 꼬리. 성적표에서 단독과 갈린다."""
JUDGE_SECONDS = 15 * 60
"""실험 원장의 채점 축(15m)."""


def hold_bars_for(bucket: Bucket) -> int:
    """갈래의 진입 유효 봉 수를 **채점 축(15m) 봉 수**로.

    Args:
        bucket: 갈래.

    Returns:
        15m 봉 수 (최소 1). 단기 15m x 12 = 12 · 스윙 1h x 10 = 40 · 장투 1d x 5 = 480.
    """
    return max(1, int(bucket.valid_bars * interval_seconds(bucket.entry) / JUDGE_SECONDS))


def structure_proposal(
    cand: Candidate | None, *, bucket: Bucket, latency_ms: int, blocked: list[str] | None = None
) -> Proposal:
    """구조 후보 → 실험 원장 제안. 후보가 없거나 RiskManager 가 막았으면 **관망**으로 남긴다.

    Args:
        cand: 후보 (롱/숏 중 화면이 고른 하나).
        bucket: 갈래 — 룰 이름에 남는다.
        latency_ms: 구조 읽기에 든 시간.
        blocked: RiskManager 가 막은 이유들. 있으면 관망.

    Returns:
        제안. 지정가 참가자라 `trigger=TOUCH` — 진입가에 닿아야 체결로 본다.
    """
    if cand is None or blocked:
        return Proposal(
            participant=STRUCTURE_NAME,
            kind=ParticipantKind.ALGORITHM,
            stance=Stance.ABSTAINED,
            stop_loss=None,
            take_profit_first=None,
            take_profit_full=None,
            avg_entry=None,
            entry_price=None,
            trigger="",
            rule=f"structure@{bucket.key}",
            conviction_pct=None,
            latency_ms=latency_ms,
            detail=" · ".join(blocked) if blocked else "구조에서 후보 없음",
            raw_text="",
        )
    return Proposal(
        participant=STRUCTURE_NAME,
        kind=ParticipantKind.ALGORITHM,
        stance=Stance.PROPOSED,
        stop_loss=cand.stop,
        take_profit_first=cand.first,
        take_profit_full=cand.target,
        avg_entry=cand.entry,
        entry_price=cand.entry,
        trigger=EntryTrigger.TOUCH.value,
        rule=f"structure@{bucket.key}",
        conviction_pct=None,
        latency_ms=latency_ms,
        detail=("숏 · " if cand.short else "롱 · ") + cand.basis,
        raw_text="",
    )


def _flag_name(raw: object) -> str:
    """깃발 하나의 이름 — dict(`key`/`label`) 도 문자열도 받는다."""
    if isinstance(raw, dict):
        row = cast("dict[str, object]", raw)
        for key in ("key", "label"):
            value = row.get(key)
            if isinstance(value, str) and value:
                return value
        return str(row)
    return str(raw)


def evidence_note_of(
    *,
    entry_frame: Timeframe,
    structure: dict[str, Any],
    extremes: dict[str, Any],
    valuation: dict[str, Any] | None,
    vix: dict[str, Any] | None,
) -> str:
    """AI+근거 참가자에게 붙이는 "우리가 읽은 것" — 숫자만, 결론은 없다.

    결론(롱/숏·가격)을 주면 모델은 그대로 베낀다 — 그러면 비교가 아니다. 구조와 맥락만 준다.

    Args:
        entry_frame: 진입 축.
        structure: `analyze` 의 `structure` 칸.
        extremes: 52주 요약.
        valuation: 재무 요약 (없으면 None).
        vix: VIX 지표 (없으면 None).

    Returns:
        프롬프트에 붙일 본문.
    """
    lines = [
        f"- 진입 축 {entry_frame.value} · 현재가 {structure.get('last')} · "
        f"ATR {structure.get('atr') or '—'}"
    ]
    sw = cast("dict[str, Any]", structure.get("swings") or {})
    for key, label in (("swing_high", "전고"), ("swing_low", "전저")):
        item = cast("dict[str, Any] | None", sw.get(key))
        if item:
            lines.append(f"- {label} {item.get('price')} (현재가 대비 {item.get('away_pct')}%)")
    for key, label in (("nearest_support", "아래 첫 지지"), ("nearest_resistance", "위 첫 저항")):
        band = cast("dict[str, Any] | None", structure.get(key))
        if band:
            lines.append(
                f"- {label} {band.get('low')}~{band.get('high')} (접점 {band.get('touches')} · "
                f"거리 {band.get('away_pct')}%)"
            )
    if extremes.get("to_high_52w_pct") is not None:
        lines.append(
            f"- 52주 고가 대비 {extremes.get('to_high_52w_pct')}% · "
            f"저가 대비 {extremes.get('to_low_52w_pct')}% · "
            f"SMA200 이격 {extremes.get('to_sma200_pct')}% · RSI(1d) {extremes.get('rsi14')}"
        )
    if valuation:
        # `/fundamentals/snapshot` 은 점수를 `score: {score, cheapness, flags}` 로 감싸고, 최상위
        # `flags` 는 dict 목록이다 — 둘 다 받는다(2026-09-11 실측: join 에 dict 가 들어가 터졌다).
        score_raw = valuation.get("score")
        score = cast("dict[str, Any]", score_raw) if isinstance(score_raw, dict) else valuation
        flags_raw = cast("list[object]", score.get("flags") or valuation.get("flags") or [])
        flags = [_flag_name(f) for f in flags_raw]
        lines.append(
            f"- 재무: 저평가 점수 {score.get('score')} · "
            f"싼 정도 {score.get('cheapness')} · "
            f"부채 깃발 {', '.join(flags) or '없음'}"
        )
    if vix and vix.get("value") is not None:
        lines.append(f"- VIX {vix.get('value')} ({vix.get('band') or ''})")
    lines.append("이것은 관측이지 결론이 아니다. 계획은 네가 세운다.")
    return "\n".join(lines)


def proposal_row(item: Proposal) -> dict[str, Any]:
    """화면 비교표 한 줄.

    Args:
        item: 제안.

    Returns:
        `{participant, kind, stance, entry, stop, first, target, conviction, detail}`.
    """
    return {
        "participant": item.participant,
        "kind": item.kind.value,
        "stance": item.stance.value,
        "entry": None if item.entry_price is None else str(item.entry_price),
        "stop": None if item.stop_loss is None else str(item.stop_loss),
        "first": None if item.take_profit_first is None else str(item.take_profit_first),
        "target": None if item.take_profit_full is None else str(item.take_profit_full),
        "conviction": item.conviction_pct,
        "trigger": item.trigger,
        "detail": item.detail[:200],
        "latency_ms": item.latency_ms,
    }


__all__ = [
    "EVIDENCE_SUFFIX",
    "STRUCTURE_NAME",
    "evidence_note_of",
    "hold_bars_for",
    "proposal_row",
    "structure_proposal",
]
