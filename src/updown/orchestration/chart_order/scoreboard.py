"""성적표 — 참가자 x 갈래 x 시장 (T273 3단계 · 순수).

실험 원장의 회차(`Cycle`)와 판정(`judgements` dict)만 받아 센다. 표본 30 미만은 회색(`grey`) —
% 를 보여 주되 믿지 말라는 표시다(표본이 판정한다 · T249 와 같은 규칙).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from updown.orchestration.ai_experiment.record import Cycle, Stance

MIN_SAMPLE = 30
"""이 아래는 회색."""
RULE_PREFIX = "structure@"


def bucket_of(cycle: Cycle) -> str:
    """회차의 갈래 — `우리-구조` 제안의 rule(`structure@swing`)에서 읽는다. 없으면 `-`.

    Args:
        cycle: 회차.

    Returns:
        갈래 키.
    """
    for p in cycle.proposals:
        if p.rule.startswith(RULE_PREFIX):
            return p.rule[len(RULE_PREFIX) :]
    return "-"


@dataclass(slots=True)
class Cell:
    """한 칸 — 참가자 x 갈래 x 시장.

    Attributes:
        participant: 참가자.
        bucket: 갈래.
        market: 시장.
        proposed: 제안한 회차 수(관망·실패 제외).
        abstained: 관망·실패 수.
        entered: 체결된 수(분모).
        no_entry: 제안했으나 기한 안에 진입가에 안 닿은 수.
        followed: 익절 먼저.
        not_followed: 손절 먼저.
        expired: 기한 만료 청산.
        net_r: 체결 건의 순 R 목록.
    """

    participant: str
    bucket: str
    market: str
    proposed: int = 0
    abstained: int = 0
    entered: int = 0
    no_entry: int = 0
    followed: int = 0
    not_followed: int = 0
    expired: int = 0
    net_r: list[Decimal] = field(default_factory=list[Decimal])

    def as_json(self) -> dict[str, Any]:
        """표 한 줄.

        Returns:
            수·비율·평균 R · `grey`(표본 30 미만).
        """
        avg = sum(self.net_r, Decimal(0)) / len(self.net_r) if self.net_r else None
        judged = self.followed + self.not_followed + self.expired
        return {
            "participant": self.participant,
            "bucket": self.bucket,
            "market": self.market,
            "proposed": self.proposed,
            "abstained": self.abstained,
            "entered": self.entered,
            "no_entry": self.no_entry,
            "followed": self.followed,
            "not_followed": self.not_followed,
            "expired": self.expired,
            "follow_pct": None if judged == 0 else round(self.followed / judged * 100, 1),
            "avg_net_r": None if avg is None else str(avg.quantize(Decimal("0.01"))),
            "grey": self.entered < MIN_SAMPLE,
        }


def scoreboard_of(
    cycles: Sequence[Cycle], verdicts: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """회차들과 판정 → 칸 목록 (참가자 · 갈래 · 시장 순).

    Args:
        cycles: 이 기능의 회차들(`우리-구조` 제안이 있는 것).
        verdicts: run_id → 판정 payload(`judgements` 목록).

    Returns:
        `Cell.as_json()` 목록. 판정이 없는 회차의 제안은 `proposed` 만 센다.
    """
    cells: dict[tuple[str, str, str], Cell] = {}

    def _cell(name: str, bucket: str, market: str) -> Cell:
        key = (name, bucket, market)
        if key not in cells:
            cells[key] = Cell(participant=name, bucket=bucket, market=market)
        return cells[key]

    for cycle in cycles:
        bucket = bucket_of(cycle)
        market = cycle.market.value
        judged: dict[str, Mapping[str, Any]] = {}
        payload = verdicts.get(cycle.run_id)
        if payload is not None:
            for row in cast("Iterable[Mapping[str, Any]]", payload.get("judgements") or []):
                judged[str(row.get("participant"))] = row
        for p in cycle.proposals:
            c = _cell(p.participant, bucket, market)
            if p.stance is not Stance.PROPOSED:
                c.abstained += 1
                continue
            c.proposed += 1
            j = judged.get(p.participant)
            if j is None:
                continue
            if not j.get("entered"):
                c.no_entry += 1
                continue
            c.entered += 1
            outcome = str(j.get("outcome") or "")
            if outcome == "FOLLOWED":
                c.followed += 1
            elif outcome == "NOT_FOLLOWED":
                c.not_followed += 1
            elif outcome == "EXPIRED":
                c.expired += 1
            raw = j.get("net_r")
            if raw is not None:
                with contextlib.suppress(ArithmeticError):
                    c.net_r.append(Decimal(str(raw)))
    return [c.as_json() for _, c in sorted(cells.items())]


__all__ = ["MIN_SAMPLE", "Cell", "bucket_of", "scoreboard_of"]
