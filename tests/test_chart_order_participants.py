"""T273 2단계 — 참가자 제안 · 근거 글 · 채점 봉 수 (순수)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from updown.common.domain.instrument import Timeframe
from updown.orchestration.ai_experiment.record import ParticipantKind, Stance
from updown.orchestration.chart_order.participants import (
    STRUCTURE_NAME,
    evidence_note_of,
    hold_bars_for,
    proposal_row,
    structure_proposal,
)
from updown.orchestration.chart_order.plans import Bucket, Candidate, load_buckets


def _bucket(key: str) -> Bucket:
    return load_buckets()[key]


def test_hold_bars_convert_bucket_bars_to_judge_frame() -> None:
    assert hold_bars_for(_bucket("short")) == 12
    assert hold_bars_for(_bucket("swing")) == 40
    assert hold_bars_for(_bucket("long")) == 480


def test_structure_proposal_is_touch_limit_and_abstains_without_candidate() -> None:
    cand = Candidate(
        short=False,
        entry=Decimal(96),
        stop=Decimal(93),
        first=Decimal(99),
        target=Decimal(102),
        basis="지지 상단",
    )
    got = structure_proposal(cand, bucket=_bucket("swing"), latency_ms=12)
    assert got.participant == STRUCTURE_NAME and got.kind is ParticipantKind.ALGORITHM
    assert (
        got.stance is Stance.PROPOSED and got.trigger == "TOUCH" and got.rule == "structure@swing"
    )
    assert (got.avg_entry, got.stop_loss, got.take_profit_first) == (
        Decimal(96),
        Decimal(93),
        Decimal(99),
    )
    assert got.actionable
    none = structure_proposal(None, bucket=_bucket("swing"), latency_ms=1)
    assert none.stance is Stance.ABSTAINED and not none.actionable
    blocked = structure_proposal(
        cand, bucket=_bucket("swing"), latency_ms=1, blocked=["비용 못 갚음"]
    )
    assert blocked.stance is Stance.ABSTAINED and "비용" in blocked.detail
    row = proposal_row(got)
    assert row["entry"] == "96" and row["stance"] == "PROPOSED"


def test_evidence_note_has_numbers_but_no_conclusion() -> None:
    note = evidence_note_of(
        entry_frame=Timeframe.H1,
        structure={
            "last": "100",
            "atr": "2",
            "swings": {"swing_high": {"price": 110, "away_pct": 10.0}, "swing_low": None},
            "nearest_support": {"low": "94", "high": "96", "touches": "3", "away_pct": "-4.0"},
            "nearest_resistance": None,
        },
        extremes={
            "to_high_52w_pct": -5.0,
            "to_low_52w_pct": 30.0,
            "to_sma200_pct": 3.0,
            "rsi14": 55.0,
        },
        valuation={
            "score": {"score": 2.5, "cheapness": 17.5, "flags": ["debt_to_equity"]},
            "flags": [{"code": "debt_to_equity", "label": "부채비율"}],
        },
        vix={"value": 17.3, "band": "안정"},
    )
    assert "전고 110" in note and "아래 첫 지지 94~96" in note and "VIX 17.3" in note
    assert "저평가 점수 2.5" in note and "결론이 아니다" in note
    for word in ("롱", "숏", "진입가", "손절가"):
        assert word not in note


def test_ai_compare_fixes_one_snapshot_and_calls_both_models_at_once() -> None:
    """2026-09-11 실측: 순차 호출이 18초 + 87초 — 스냅샷을 먼저 고정하고 둘을 동시에 던진다.
    같은 봉을 봐야 비교이므로 `snapshot=` 두 번, `asyncio.gather` 한 번."""
    from pathlib import Path

    source = Path("src/updown/apps/api/chart_order.py").read_text(encoding="utf-8")
    body = source[
        source.index("async def _work(") : source.index(
            '_logger.info(\n            "chart_order_run"'
        )
    ]
    assert "await fetch_snapshot(" in body
    assert "asyncio.gather(" in body
    assert body.count("snapshot=snapshot") == 2
    assert "snapshot=alone.snapshot" not in body


def test_plan_reasons_tell_why_there_is_no_candidate() -> None:
    """후보 없음이 원래 없는 자리인지 오류인지 — 레벨이 어디서 떨어졌는지로 말한다 (2026-09-11)."""
    from updown.apps.api.chart_order import plan_reasons

    none: dict[str, dict[str, Any] | None] = {"long": None, "short": None}
    got = plan_reasons(
        plans=none,
        support=None,
        resistance=None,
        short_allowed=False,
        dropped={"raw": 5, "kept": 0, "stale": 2, "broken": 2, "few_touches": 1, "too_close": 0},
    )
    assert (
        got["long"] is not None and "5개가 전부 걸러졌다" in got["long"] and "관통 2" in got["long"]
    )
    assert got["short"] == "이 시장은 숏이 없다(현물)"
    got = plan_reasons(
        plans=none, support=None, resistance=None, short_allowed=True, dropped={"raw": 0}
    )
    assert got["long"] is not None and "하나도 못 찾았다" in got["long"]
    assert got["short"] is not None and "위 첫 저항이 없다" in got["short"]
    assert plan_reasons(
        plans={"long": {"ok": True}, "short": {"ok": True}},
        support={"low": "1"},
        resistance={"low": "2"},
        short_allowed=True,
        dropped={"raw": 3, "kept": 2},
    ) == {"long": None, "short": None}
