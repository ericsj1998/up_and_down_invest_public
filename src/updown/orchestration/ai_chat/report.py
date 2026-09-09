"""AI 퍼포먼스 리포트 — 참가자 채점 (T249 · 순수).

참가자 = **모델 x 프롬프트 버전 x 프롬프트 해시 x 스냅샷 설정**. 프롬프트 본문이 한 글자라도
바뀌면 해시가
바뀌어 **새 참가자**가 된다 — 버전 번호를 올리는 것을 잊어도 표본이 섞이지 않는다 (Phase 5 원칙).

채점 원료는 AI 판(`wf_runs.meta_json.ai`)의 끝난 매매(`wf_trades`)와 채팅 턴 기록
(`event_logs.ai_chat_turn` 의 토큰). 지표는 백테스트 리포트와 같은 것만 — 표본 n · 방향 적중률 ·
평균 R · 손익(%) · **MDD 병기** · 토큰. 표본 30 미만은 **판정 보류**(회색)다.

⛔ 여기서 모델을 고르지 않는다. `default_model` 은 관문(n≥30 · 기준선 위)을 지난 참가자를
**돌려줄 뿐**
이고, 실제로 기본 모델을 바꾸는 것은 사용자 확정 뒤(`apps/api/ai_chat.settings`)다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from updown.orchestration.ai_chat.agent import PROMPT_VERSION, SYSTEM_PROMPT
from updown.orchestration.ai_chat.tools import TOOLS
from updown.orchestration.walkforward.ledger import Outcome, TradeRecord

MIN_SAMPLE = 30
"""판정 표본 하한 — CLAUDE.md "표본 30 은 바닥이지 안전선이 아니다"."""

DEFAULT_SNAPSHOT: dict[str, Any] = {"bars": 60, "ohlc": 12, "frames": ["1d", "1h"]}
"""스냅샷 설정 — `snapshot.summarize_frame` 의 봉 수·압축 OHLC 수·기본 축.

한 축씩만 바꾼다(규칙 #12)."""

EXPERIMENT_EVENT = "ai_experiment_started"
"""시작 스위치 — `event_logs` 는 추가만 되므로 켜면 되돌릴 수 없다."""


def prompt_fingerprint(prompt: str = SYSTEM_PROMPT, tool_names: Sequence[str] | None = None) -> str:
    """프롬프트 본문 + 도구 이름 목록의 해시 앞 8자리.

    Args:
        prompt: 시스템 프롬프트.
        tool_names: 도구 이름들. None 이면 지금 등록된 `TOOLS`.

    Returns:
        16진수 8자리. 도구가 늘거나 문장이 바뀌면 달라진다.
    """
    names = list(tool_names) if tool_names is not None else [t.spec.name for t in TOOLS]
    raw = json.dumps({"prompt": prompt, "tools": sorted(names)}, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def snapshot_key(config: dict[str, Any] | None = None) -> str:
    """스냅샷 설정의 짧은 키.

    Args:
        config: 스냅샷 설정. None 이면 기본.

    Returns:
        `b60-o12-1d.1h` 꼴.
    """
    found = config or DEFAULT_SNAPSHOT
    frames = ".".join(str(f) for f in found.get("frames", []))
    return f"b{found.get('bars')}-o{found.get('ohlc')}-{frames}"


def participant_key(
    model: str,
    *,
    prompt_version: str = PROMPT_VERSION,
    fingerprint: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> str:
    """참가자 키 — `모델@프롬프트버전#해시/스냅샷`.

    Args:
        model: 모델 id.
        prompt_version: 프롬프트 버전 문자열.
        fingerprint: 프롬프트 해시. None 이면 지금 것.
        snapshot: 스냅샷 설정. None 이면 기본.

    Returns:
        키. 판 메타 `ai.participant` 와 `ai_participants.id` 가 이 값이다.
    """
    digest = fingerprint or prompt_fingerprint()
    return f"{model}@{prompt_version}#{digest}/{snapshot_key(snapshot)}"


@dataclass(frozen=True, slots=True)
class AiTrade:
    """채점에 쓰는 매매 한 건 — 원장 기록에서 필요한 것만.

    Attributes:
        participant: 참가자 키 (판 메타 `ai.participant`).
        closed_at: 청산 시각.
        gain_pct: 비용 뺀 실현 수익률(%).
        realized_rr: 실현 R. 계획이 성립하지 않으면 None.
        won: 방향이 맞았나 (수익률 > 0).
        outcome: 결과 문자열.
        symbol: 종목.
        reasons: 제안 때 붙인 근거 문장들 (판 메타 `ai.reasons`).
        run_key: 판 키.
    """

    participant: str
    closed_at: datetime
    gain_pct: Decimal
    realized_rr: Decimal | None
    won: bool
    outcome: str
    symbol: str
    reasons: tuple[str, ...] = ()
    run_key: str = ""


def trade_of(
    record: TradeRecord,
    *,
    participant: str,
    symbol: str,
    reasons: Sequence[str] = (),
    run_key: str = "",
) -> AiTrade | None:
    """원장 기록 → 채점 행. 안 끝났거나 취소면 None.

    Args:
        record: 원장 기록.
        participant: 참가자 키.
        symbol: 종목.
        reasons: 판 메타의 근거 문장들.
        run_key: 판 키.

    Returns:
        채점 행. 끝나지 않았거나 취소·확인 실패면 None.
    """
    if record.closed_at is None or record.outcome in (Outcome.CANCELLED, Outcome.CONFIRM_FAIL):
        return None
    gain = record.gain_pct
    if gain is None:
        return None
    return AiTrade(
        participant=participant,
        closed_at=record.closed_at,
        gain_pct=gain,
        realized_rr=record.realized_rr,
        won=gain > 0,
        outcome=str(record.outcome),
        symbol=symbol,
        reasons=tuple(str(r) for r in reasons),
        run_key=run_key,
    )


@dataclass(frozen=True, slots=True)
class TurnStats:
    """참가자의 채팅 턴 합계 — 토큰·건수 (원가 축) + 환각 칸 수 (T256 · 2차).

    Attributes:
        turns: 턴 수.
        prompt_tokens: 입력 토큰 합.
        completion_tokens: 출력 토큰 합.
        failures: 모델 실패 턴 수.
        dashboard_missing: 대시보드에서 근거 없던 칸 수의 합 — 참조가 안 풀린 칸이라
            **환각 후보**다(값이 화면에 나가지는 않았다).
    """

    turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failures: int = 0
    dashboard_missing: int = 0


@dataclass(frozen=True, slots=True)
class Scorecard:
    """참가자 성적표 한 줄.

    Attributes:
        participant: 키.
        n: 끝난 매매 수 — **판정 표본**.
        wins: 방향 적중 수.
        hit_rate: 적중률(%). n=0 이면 None.
        avg_r: 평균 실현 R. 없으면 None.
        pnl_pct: 수익률 합(%) — 매매마다 같은 예산이라 단순 합이다.
        mdd_pct: 누적 수익률 곡선의 최대 낙폭(%).
        judged: n ≥ MIN_SAMPLE.
        turns: 채팅 턴 합계.
        last_closed_at: 마지막 청산.
        trades: 매매 목록 (최근 것이 뒤).
    """

    participant: str
    n: int
    wins: int
    hit_rate: Decimal | None
    avg_r: Decimal | None
    pnl_pct: Decimal
    mdd_pct: Decimal
    judged: bool
    turns: TurnStats = field(default_factory=TurnStats)
    last_closed_at: datetime | None = None
    trades: tuple[AiTrade, ...] = ()

    def as_json(self) -> dict[str, Any]:
        """화면용 dict.

        Returns:
            Decimal 은 문자열 · 자본 곡선(`curve`)은 누적 % 목록.
        """
        return {
            "participant": self.participant,
            "n": self.n,
            "wins": self.wins,
            "hit_rate": None if self.hit_rate is None else str(self.hit_rate),
            "avg_r": None if self.avg_r is None else str(self.avg_r),
            "pnl_pct": str(self.pnl_pct),
            "mdd_pct": str(self.mdd_pct),
            "judged": self.judged,
            "min_sample": MIN_SAMPLE,
            "turns": self.turns.turns,
            "prompt_tokens": self.turns.prompt_tokens,
            "completion_tokens": self.turns.completion_tokens,
            "failures": self.turns.failures,
            "dashboard_missing": self.turns.dashboard_missing,
            "last_closed_at": None
            if self.last_closed_at is None
            else self.last_closed_at.isoformat(),
            "curve": [str(v) for v in equity_curve(self.trades)],
        }


def equity_curve(trades: Sequence[AiTrade]) -> list[Decimal]:
    """청산 순서대로 누적 수익률(%).

    Args:
        trades: 끝난 매매들.

    Returns:
        0 에서 시작하는 누적 % 목록 (길이 = 매매 수 + 1).
    """
    out = [Decimal(0)]
    for trade in sorted(trades, key=lambda t: t.closed_at):
        out.append(out[-1] + trade.gain_pct)
    return out


def max_drawdown(curve: Sequence[Decimal]) -> Decimal:
    """누적 %곡선의 최대 낙폭(%).

    Args:
        curve: 누적 % 목록.

    Returns:
        고점 대비 %포인트 차의 최댓값. 늘 0 이상.
    """
    peak = Decimal(0)
    worst = Decimal(0)
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def _quant(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def scorecard(participant: str, trades: Sequence[AiTrade], turns: TurnStats) -> Scorecard:
    """매매 목록 → 성적표.

    Args:
        participant: 참가자 키.
        trades: 그 참가자의 끝난 매매들.
        turns: 채팅 턴 합계.

    Returns:
        성적표. 표본이 30 미만이면 `judged` 가 거짓이다.
    """
    ordered = tuple(sorted(trades, key=lambda t: t.closed_at))
    n = len(ordered)
    wins = sum(1 for t in ordered if t.won)
    rr = [t.realized_rr for t in ordered if t.realized_rr is not None]
    curve = equity_curve(ordered)
    return Scorecard(
        participant=participant,
        n=n,
        wins=wins,
        hit_rate=None if n == 0 else _quant(Decimal(wins) / n * 100),
        avg_r=None if not rr else _quant(sum(rr, Decimal(0)) / len(rr)),
        pnl_pct=_quant(curve[-1]),
        mdd_pct=_quant(max_drawdown(curve)),
        judged=n >= MIN_SAMPLE,
        turns=turns,
        last_closed_at=ordered[-1].closed_at if ordered else None,
        trades=ordered,
    )


def tabulate(
    trades: Iterable[AiTrade],
    turns: dict[str, TurnStats] | None = None,
    *,
    known: Iterable[str] = (),
) -> list[Scorecard]:
    """참가자별 성적표 — 표본 많은 순, 같으면 키 순.

    Args:
        trades: 채점 행들.
        turns: 참가자 → 턴 합계.
        known: 등록됐지만 매매가 없는 참가자도 0 행으로 싣는다.

    Returns:
        성적표들.
    """
    by_key: dict[str, list[AiTrade]] = {key: [] for key in known}
    for trade in trades:
        by_key.setdefault(trade.participant, []).append(trade)
    stats = turns or {}
    for key in stats:
        by_key.setdefault(key, [])
    cards = [scorecard(key, items, stats.get(key, TurnStats())) for key, items in by_key.items()]
    cards.sort(key=lambda c: (-c.n, c.participant))
    return cards


def turn_stats(events: Iterable[dict[str, Any]]) -> dict[str, TurnStats]:
    """`ai_chat_turn` 페이로드들 → 참가자별 토큰 합계.

    Args:
        events: 이벤트 페이로드들.

    Returns:
        참가자 키 → 합계.

    옛 턴(참가자 키가 없던 것)은 `모델@버전` 으로 묶인다 — 해시가 없어 지금 참가자와 합쳐지지
    않는다.
    """
    out: dict[str, TurnStats] = {}
    for payload in events:
        key = str(
            payload.get("participant") or f"{payload.get('model')}@{payload.get('prompt_version')}"
        )
        raw_tokens: object = payload.get("tokens")
        tokens_dict: dict[str, Any] = (
            cast("dict[str, Any]", raw_tokens) if isinstance(raw_tokens, dict) else {}
        )
        found = out.get(key, TurnStats())
        out[key] = TurnStats(
            turns=found.turns + 1,
            prompt_tokens=found.prompt_tokens + int(tokens_dict.get("prompt") or 0),
            completion_tokens=found.completion_tokens + int(tokens_dict.get("completion") or 0),
            failures=found.failures + (1 if payload.get("failure") else 0),
            dashboard_missing=found.dashboard_missing + int(payload.get("dashboard_missing") or 0),
        )
    return out


def reason_hits(trades: Iterable[AiTrade]) -> list[dict[str, Any]]:
    """근거 문장별 적중 수 — 매매일지의 "어느 근거가 맞았나" (T248 3차 · T249 원료).

    Args:
        trades: 끝난 AI 매매들.

    Returns:
        `[{reason, n, wins, hit_rate}]` — 표본 많은 순. 같은 근거가 여러 매매에 붙으면 그만큼 센다.
    """
    counts: dict[str, list[int]] = {}
    for trade in trades:
        for reason in trade.reasons:
            found = counts.setdefault(reason, [0, 0])
            found[0] += 1
            found[1] += 1 if trade.won else 0
    ordered = sorted(counts.items(), key=lambda item: (-item[1][0], item[0]))
    return [
        {
            "reason": reason,
            "n": n,
            "wins": wins,
            "hit_rate": str(_quant(Decimal(wins) / n * 100)) if n else None,
        }
        for reason, (n, wins) in ordered
    ]


def journal_rows(trades: Iterable[AiTrade]) -> list[dict[str, Any]]:
    """매매일지 행 — 청산 순서 (오래된 것이 앞).

    Args:
        trades: 끝난 AI 매매들.

    Returns:
        `[{closed_at, symbol, participant, outcome, gain_pct, realized_rr, won, reasons, run_key}]`.
    """
    return [
        {
            "closed_at": t.closed_at.isoformat(),
            "symbol": t.symbol,
            "participant": t.participant,
            "outcome": t.outcome,
            "gain_pct": str(_quant(t.gain_pct)),
            "realized_rr": None if t.realized_rr is None else str(_quant(t.realized_rr)),
            "won": t.won,
            "reasons": list(t.reasons),
            "run_key": t.run_key,
        }
        for t in sorted(trades, key=lambda t: t.closed_at)
    ]


@dataclass(frozen=True, slots=True)
class Baseline:
    """기준선 — 사람·시스템 판의 같은 지표 (앙상블 기준선)."""

    n: int
    hit_rate: Decimal | None
    avg_r: Decimal | None


def default_model(cards: Sequence[Scorecard], baseline: Baseline) -> str | None:
    """채팅 기본 모델 관문 — n ≥ 30 · 적중률과 R 이 기준선 위인 참가자 중 R 최대.

    Args:
        cards: 성적표들.
        baseline: 기준선. 기준선 값이 없으면(None) 그 축은 비교하지 않는다.

    Returns:
        모델 id. 지나는 참가자가 없으면 None — 그러면 풀의 `chat` 기본값 그대로다.
    """
    passing: list[Scorecard] = []
    for card in cards:
        if not card.judged or card.hit_rate is None or card.avg_r is None:
            continue
        if baseline.hit_rate is not None and card.hit_rate <= baseline.hit_rate:
            continue
        if baseline.avg_r is not None and card.avg_r <= baseline.avg_r:
            continue
        passing.append(card)
    if not passing:
        return None
    best = max(passing, key=lambda c: (c.avg_r or Decimal(0), c.n))
    return best.participant.split("@", 1)[0]


__all__ = [
    "DEFAULT_SNAPSHOT",
    "EXPERIMENT_EVENT",
    "MIN_SAMPLE",
    "AiTrade",
    "Baseline",
    "Scorecard",
    "TurnStats",
    "default_model",
    "equity_curve",
    "journal_rows",
    "max_drawdown",
    "participant_key",
    "prompt_fingerprint",
    "reason_hits",
    "scorecard",
    "snapshot_key",
    "tabulate",
    "trade_of",
    "turn_stats",
]
