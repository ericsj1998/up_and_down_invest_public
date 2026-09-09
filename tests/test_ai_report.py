"""T249 — 참가자 키·프롬프트 해시 잠금 · 채점(적중률·R·손익·MDD) · 관문 · 토큰 합계."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.orchestration.ai_chat.agent import PROMPT_VERSION, SYSTEM_PROMPT
from updown.orchestration.ai_chat.report import (
    MIN_SAMPLE,
    AiTrade,
    Baseline,
    TurnStats,
    default_model,
    equity_curve,
    max_drawdown,
    participant_key,
    prompt_fingerprint,
    scorecard,
    snapshot_key,
    tabulate,
    trade_of,
    turn_stats,
)
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Outcome,
    TradeRecord,
)

PROMPT_LOCK = {"chat-1.0": "0838d7c4"}
"""🔴 프롬프트 잠금 — `SYSTEM_PROMPT` 나 도구 목록을 바꾸면 해시가 달라져 이 시험이 깨진다.

그때 할 일은 **버전을 올리고**(`PROMPT_VERSION`) 여기에 새 짝을 **추가**하는 것이다. 값을 고쳐
넘기면
옛 참가자의 성과가 새 프롬프트의 것으로 읽힌다 (Phase 5 원칙 · T249)."""

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _trade(i: int, gain: str, *, rr: str | None = "1.0", key: str = "m@chat-1.0#h/s") -> AiTrade:
    return AiTrade(
        participant=key,
        closed_at=T0 + timedelta(hours=i),
        gain_pct=Decimal(gain),
        realized_rr=None if rr is None else Decimal(rr),
        won=Decimal(gain) > 0,
        outcome="목표 익절" if Decimal(gain) > 0 else "손절",
        symbol="NVDA",
    )


class TestParticipantKey:
    def test_prompt_hash_is_locked_to_version(self) -> None:
        assert PROMPT_VERSION in PROMPT_LOCK, "새 버전이면 PROMPT_LOCK 에 짝을 추가한다"
        assert prompt_fingerprint() == PROMPT_LOCK[PROMPT_VERSION], (
            "프롬프트(또는 도구 목록)가 바뀌었다 — PROMPT_VERSION 을 올리고 PROMPT_LOCK 에 "
            "새 짝을 추가한다"
        )

    def test_changed_prompt_makes_new_participant(self) -> None:
        before = participant_key("m")
        after = participant_key("m", fingerprint=prompt_fingerprint(SYSTEM_PROMPT + " "))
        assert before != after and before.startswith(f"m@{PROMPT_VERSION}#")
        assert prompt_fingerprint(SYSTEM_PROMPT, ["a"]) != prompt_fingerprint(
            SYSTEM_PROMPT, ["a", "b"]
        )

    def test_snapshot_axis_is_in_key(self) -> None:
        assert snapshot_key() == "b60-o12-1d.1h"
        assert participant_key("m", snapshot={"bars": 120, "ohlc": 12, "frames": ["1d"]}).endswith(
            "/b120-o12-1d"
        )


class TestScoring:
    def test_curve_and_mdd(self) -> None:
        curve = equity_curve([_trade(0, "2"), _trade(1, "-3"), _trade(2, "1"), _trade(3, "-1")])
        assert curve == [Decimal(0), Decimal(2), Decimal(-1), Decimal(0), Decimal(-1)]
        assert max_drawdown(curve) == Decimal(3)

    def test_scorecard_values_and_sample_gate(self) -> None:
        trades = [_trade(i, "1" if i % 3 else "-1", rr="2" if i % 3 else "-1") for i in range(9)]
        card = scorecard("m@chat-1.0#h/s", trades, TurnStats(turns=4, prompt_tokens=100))
        assert card.n == 9 and card.wins == 6 and card.hit_rate == Decimal("66.67")
        assert card.avg_r == Decimal("1.00") and card.pnl_pct == Decimal("3.00")
        assert card.judged is False and card.turns.turns == 4
        big = scorecard("x", [_trade(i, "1") for i in range(MIN_SAMPLE)], TurnStats())
        assert big.judged is True and big.mdd_pct == Decimal(0)
        payload = card.as_json()
        assert payload["min_sample"] == MIN_SAMPLE and len(payload["curve"]) == 10

    def test_tabulate_includes_known_and_turn_only_participants(self) -> None:
        cards = tabulate(
            [_trade(0, "1", key="a"), _trade(1, "1", key="a"), _trade(2, "-1", key="b")],
            {"c": TurnStats(turns=2)},
            known=["d"],
        )
        assert [c.participant for c in cards] == ["a", "b", "c", "d"]
        assert cards[2].n == 0 and cards[2].turns.turns == 2

    def test_trade_of_skips_open_and_cancelled(self) -> None:
        base = TradeRecord(
            trade_id="t1",
            playbook="custom",
            actor=Actor.AI,
            placed_at=T0,
            entry=Decimal(100),
            planned_stop=Decimal(95),
            planned_target=Decimal(110),
            direction=Direction.LONG,
            cost_pct=Decimal("0.001"),
        )
        assert trade_of(base, participant="k", symbol="NVDA") is None
        done = base.closed(
            at=T0 + timedelta(hours=1), price=Decimal(110), outcome=Outcome.TAKE_PROFIT
        )
        got = trade_of(done, participant="k", symbol="NVDA")
        assert got is not None and got.won and got.realized_rr == Decimal(2)
        cancelled = base.closed(at=T0, price=Decimal(100), outcome=Outcome.CANCELLED)
        assert trade_of(cancelled, participant="k", symbol="NVDA") is None

    def test_turn_stats_groups_old_and_new_keys(self) -> None:
        got = turn_stats(
            [
                {"participant": "m@v#h/s", "tokens": {"prompt": 10, "completion": 2}},
                {"participant": "m@v#h/s", "tokens": {"prompt": 5}, "failure": "TIMEOUT"},
                {"model": "m", "prompt_version": "v", "tokens": {}},
            ]
        )
        assert got["m@v#h/s"] == TurnStats(
            turns=2, prompt_tokens=15, completion_tokens=2, failures=1
        )
        assert got["m@v"].turns == 1


class TestGate:
    def test_default_model_requires_sample_and_beats_baseline(self) -> None:
        strong = scorecard(
            "s@v#h/s", [_trade(i, "1", rr="1.5") for i in range(MIN_SAMPLE)], TurnStats()
        )
        small = scorecard("t@v#h/s", [_trade(i, "5", rr="9") for i in range(5)], TurnStats())
        base = Baseline(n=100, hit_rate=Decimal(50), avg_r=Decimal("0.5"))
        assert default_model([small, strong], base) == "s"
        assert default_model([small], base) is None
        assert default_model([strong], Baseline(100, Decimal(100), Decimal(0))) is None
        assert default_model([strong], Baseline(0, None, None)) == "s"
