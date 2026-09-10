"""T273 3단계 — 성적표 집계 · 상한 설정 (순수)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from updown.common.domain.instrument import Market, Timeframe
from updown.orchestration.ai_experiment.record import (
    Cycle,
    CycleSource,
    ParticipantKind,
    Proposal,
    Stance,
)
from updown.orchestration.chart_order.plans import load_limits
from updown.orchestration.chart_order.scoreboard import MIN_SAMPLE, bucket_of, scoreboard_of


def _prop(name: str, kind: ParticipantKind, stance: Stance, rule: str = "") -> Proposal:
    proposed = stance is Stance.PROPOSED
    return Proposal(
        participant=name,
        kind=kind,
        stance=stance,
        stop_loss=Decimal(95) if proposed else None,
        take_profit_first=Decimal(105) if proposed else None,
        take_profit_full=Decimal(110) if proposed else None,
        avg_entry=Decimal(100) if proposed else None,
        entry_price=Decimal(100) if proposed else None,
        trigger="MARKET" if proposed else "",
        rule=rule,
        conviction_pct=None,
        latency_ms=1,
        detail="",
        raw_text="",
    )


def _cycle(run_id: str, *proposals: Proposal) -> Cycle:
    return Cycle(
        run_id=run_id,
        symbol="AAPL",
        market=Market.NASDAQ,
        timeframe=Timeframe.M15,
        taken_at=datetime(2026, 9, 11, tzinfo=UTC),
        digest="d",
        entry=Decimal(100),
        last_bar_ts=datetime(2026, 9, 11, tzinfo=UTC),
        atr=Decimal(1),
        hold_bars=40,
        prompt_version="1.0",
        proposals=proposals,
        source=CycleSource.MANUAL,
    )


def test_limits_load() -> None:
    got = load_limits()
    assert got.runs_per_user_per_day == 20 and got.reuse_minutes == 10


def test_scoreboard_counts_by_participant_bucket_market() -> None:
    ours = _prop("우리-구조", ParticipantKind.ALGORITHM, Stance.PROPOSED, rule="structure@swing")
    ai = _prop("m", ParticipantKind.LLM, Stance.PROPOSED)
    ai_note = _prop("m+근거", ParticipantKind.LLM, Stance.FAILED)
    c1 = _cycle("r1", ours, ai, ai_note)
    c2 = _cycle("r2", ours, ai)
    assert bucket_of(c1) == "swing"
    verdicts = {
        "r1": {
            "judgements": [
                {
                    "participant": "우리-구조",
                    "entered": True,
                    "outcome": "FOLLOWED",
                    "net_r": "1.8",
                },
                {"participant": "m", "entered": False, "outcome": None, "net_r": None},
            ]
        }
        # r2 는 아직 판정 없음
    }
    rows = {
        (r["participant"], r["bucket"], r["market"]): r for r in scoreboard_of([c1, c2], verdicts)
    }
    ours_row = rows[("우리-구조", "swing", "NASDAQ")]
    assert (ours_row["proposed"], ours_row["entered"], ours_row["followed"]) == (2, 1, 1)
    assert ours_row["follow_pct"] == 100.0 and ours_row["avg_net_r"] == "1.80"
    assert ours_row["grey"] is True and MIN_SAMPLE == 30
    ai_row = rows[("m", "swing", "NASDAQ")]
    assert (ai_row["proposed"], ai_row["no_entry"], ai_row["entered"]) == (2, 1, 0)
    assert ai_row["follow_pct"] is None
    assert rows[("m+근거", "swing", "NASDAQ")]["abstained"] == 1
