"""라이브 실험 채점 — **우리 판정기를 그대로 쓴다** (Phase 5 §5-5).

## 새 판정 로직을 만들지 않는다

> `LlmProposal (진입·손절·익절) → judge() → FOLLOWED / NOT_FOLLOWED / EXPIRED + realized_r`
> `analysis/evaluation/follow_through.py` 가 우리 셋업에 쓰는 것과 **완전히 같은 함수**다.

여기서 "LLM 용 간이 판정"을 새로 쓰면 두 참가자가 다른 자에 놓인다. 같은 봉에 손절과
익절을 둘 다 건드렸을 때 손절이 이긴다는 규칙(D1-4) 하나만 달라도 승률이 몇 %p 씩
움직인다. 그래서 `judge()` 를 부르기 위해 **제안을 `TradeSetup` 으로 되살린다**.

## 측정 전에 선언한 값들 (§5.6.2)

| 항목 | 값 | 근거 |
|---|---|---|
| 채점 목표 | **1차 익절가** | §5.6.6 의 이행 정의 |
| 같은 봉 충돌 | **손절 우선** | D1-4 |
| 보유 기한 | 체결 후 `hold_bars` 봉, 만료 시 **종가 청산** | §1-0k |
| 진입 대기 상한 | **16봉 (= 회차 간격 4시간)** | 아래 |
| 비용 | 왕복 실측치를 **R 로 환산해 차감** | §12.7 · G-AI-3 |
| 확신도 부분집합 | **60% 이상** | 아래 |

**진입 대기 상한이 회차 간격인 이유**는 구조적이다 — 4시간 뒤 다음 회차가 같은 종목을
다시 탐지하므로, 그때까지 체결되지 않은 제안은 **다음 제안으로 대체된다**. 성과를 보고
고른 값이 아니라 실험의 주기가 정하는 값이다.

**확신도 60% 컷을 지금 적는 이유**는 나중에 적으면 그것이 자동조율이기 때문이다.
"확신도 높은 것만 보면 좋아지더라"는 결과를 본 뒤에 만든 부분집합이고, 그런 식으로는
어떤 무작위 계열에서도 좋은 부분집합을 찾을 수 있다. 이 컷도 §12.9 표본 30건을 따로
채워야 판정에 쓴다.

## 🔴 비용을 빼지 않으면 전부 좋아 보인다

왕복 0.157%(업비트 실측)는 손절폭이 좁을수록 R 로 환산했을 때 커진다. 손절을 0.3% 에
둔 제안은 거래당 **0.52R** 을 비용으로 낸다 — 승률 60% RR 1.0 이어도 적자다.
그래서 표의 판정 칸은 언제나 **순R** 이다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from updown.analysis.evaluation.follow_through import Outcome, find_entry, judge
from updown.analysis.indicators.atr import atr as atr_series
from updown.common.costs import CostTable
from updown.common.domain.candle import Candle
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.common.numeric import fixed_context
from updown.orchestration.ai_experiment.participants import MARKET_TRIGGER
from updown.orchestration.ai_experiment.record import (
    Cycle,
    ParticipantKind,
    Proposal,
    Stance,
)

ENTRY_WAIT_BARS = 16
"""체결을 기다리는 상한 (봉). ⛔ 성과로 조정 금지 — 회차 간격이 정하는 값이다."""

CONVICTION_CUT = 60
"""확신도 부분집합의 하한 (%). ⛔ **측정 전 선언값**이다 (§5.6.2)."""

MIN_SAMPLE = 30
"""판정에 필요한 표본 (§12.9). 미달이면 **수치는 싣되 판정하지 않는다**."""


class NotMaturedError(ValueError):
    """아직 판정할 만큼 봉이 쌓이지 않았다.

    Note:
        조용히 `UNRESOLVED` 로 적고 넘어가면 그 회차가 **영원히 미판정**으로 굳는다 —
        원장이 불변이라 다시 쓸 수 없기 때문이다 (`record.Ledger.save_verdict`).
        그래서 예외로 올려 호출부가 다음 주기에 다시 시도하게 한다.
    """


@dataclass(frozen=True, slots=True)
class Judgement:
    """참가자 하나의 회차 결과.

    Attributes:
        participant: 참가자.
        kind: 종류.
        stance: 그 회차에 한 일.
        entered: 체결됐는가. 관망·실패·미체결이면 False.
        outcome: 이행 판정. 체결되지 않았으면 None.
        bars_to_entry: 제안에서 체결까지 걸린 봉 수. 시장가는 0.
        bars_held: 보유 봉 수.
        planned_rr: 제안이 내건 손익비 `(1차익절-평단)/(평단-손절)`.
        risk_pct: 1R 의 크기 — 평단 대비 손절 거리. **비용 환산의 분모**다.
        gross_r: 비용 전 실현 R.
        cost_r: R 로 환산한 왕복 비용.
        net_r: 비용 후 순 R — **판정에 쓰는 값**이다 (G-AI-3).
        conviction_pct: 확신도. 부분집합 계산에 쓴다.
    """

    participant: str
    kind: ParticipantKind
    stance: Stance
    entered: bool
    outcome: Outcome | None
    bars_to_entry: int | None
    bars_held: int | None
    planned_rr: Decimal | None
    risk_pct: Decimal | None
    gross_r: Decimal | None
    cost_r: Decimal | None
    net_r: Decimal | None
    conviction_pct: int | None


def rebuild_setup(proposal: Proposal) -> TradeSetup:
    """제안을 판정 가능한 `TradeSetup` 으로 되살린다.

    Args:
        proposal: 채점 가능한 제안 (`actionable` 이어야 한다).

    Returns:
        셋업. `judge()`/`find_entry()` 가 읽는 칸만 진짜 값이다.

    Raises:
        ValueError: 제안이 채점 가능한 상태가 아닌 경우.

    Note:
        🔴 **탐지기 흉내가 아니다.** `judge()` 는 `avg_entry`·`stop_loss`·`tp_ladder[0]`
        만 읽고, `find_entry()` 는 `entry_trigger`·`entry_plan[0]` 만 읽는다. 나머지 칸
        (`confidence`·`evidence`·`stop_candidates`)은 판정에 관여하지 않으므로 비워 둔다 —
        그럴듯한 값을 채우면 이 객체가 진짜 탐지 결과처럼 보여 나중에 오독된다.

        `stop_candidates` 를 비우는 것은 `TradeSetup.__post_init__` 이 허용하는 경로다
        (하위호환). 여기에 `stop_loss` 를 후보로 넣으면 **탐지기가 내놓지 않은 값을
        관측 사실로 위장**하는 것이 되어 불변식의 취지를 정면으로 어긴다.
    """
    if not proposal.actionable or proposal.avg_entry is None:
        raise ValueError(f"{proposal.participant}: 채점 가능한 제안이 아니다")
    stop = proposal.stop_loss
    target = proposal.take_profit_first
    if stop is None or target is None:
        raise ValueError(f"{proposal.participant}: 손절·익절이 비었다")

    leg = proposal.entry_price if proposal.entry_price is not None else proposal.avg_entry
    trigger = (
        EntryTrigger.CLOSE_CONFIRM
        if proposal.trigger == MARKET_TRIGGER
        else EntryTrigger(proposal.trigger)
    )
    with fixed_context():
        rr = (target - proposal.avg_entry) / (proposal.avg_entry - stop)
    return TradeSetup(
        setup_type=proposal.kind.value,
        rule_version=proposal.rule or f"{proposal.participant}@live",
        entry_trigger=trigger,
        entry_plan=(EntryLeg(price=leg, ratio=Decimal(1)),),
        avg_entry=proposal.avg_entry,
        stop_loss=stop,
        tp_ladder=(TakeProfitStep(price=target, ratio=Decimal(1), then=None),),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
        rr_ratio=rr,
        confidence=0.0,
        evidence=(),
    )


def _entry_index(
    candles: Sequence[Candle],
    proposal: Proposal,
    setup: TradeSetup,
    detect_index: int,
) -> int | None:
    """체결 봉을 찾는다.

    Args:
        candles: 전체 캔들.
        proposal: 제안.
        setup: 되살린 셋업.
        detect_index: 스냅샷 마지막 봉의 번호.

    Returns:
        체결 봉 번호. 대기 상한 안에 체결되지 않으면 None.

    Note:
        시장가 참가자는 **다음 봉**에서 체결한다 (§4.2 봉마감 확정 → 다음 봉 집행).
        스냅샷의 마지막 봉은 진행 중이라 그 봉의 고가·저가에 스냅샷 **이전** 움직임이
        섞여 있다 — 채점에 넣으면 이미 지나간 가격으로 손절이 잡힌다 (`record.Cycle`).
    """
    if proposal.trigger == MARKET_TRIGGER:
        entry = detect_index + 1
        return entry if entry < len(candles) else None

    series = atr_series(
        [item.high for item in candles],
        [item.low for item in candles],
        [item.close for item in candles],
    )
    found = find_entry(candles, setup, after=detect_index, atr_series=series)
    if found is None or found - detect_index > ENTRY_WAIT_BARS:
        return None
    return found


def resolve_cycle(cycle: Cycle, candles: Sequence[Candle], costs: CostTable) -> list[Judgement]:
    """한 회차를 판정한다.

    Args:
        cycle: 회차.
        candles: 채점 시간축 캔들. **스냅샷 구간과 그 이후를 모두** 포함해야 한다.
        costs: 비용 테이블.

    Returns:
        참가자별 판정.

    Raises:
        NotMaturedError: 스냅샷 마지막 봉을 찾을 수 없거나, 기한을 채울 봉이 없는 경우.
    """
    detect_index = next(
        (index for index, item in enumerate(candles) if item.ts == cycle.last_bar_ts), None
    )
    if detect_index is None:
        raise NotMaturedError(
            f"{cycle.run_id}: 스냅샷 마지막 봉 {cycle.last_bar_ts} 가 캔들에 없다"
        )

    needed = detect_index + ENTRY_WAIT_BARS + cycle.hold_bars + 1
    if len(candles) <= needed:
        raise NotMaturedError(
            f"{cycle.run_id}: {needed + 1}봉이 필요한데 {len(candles)}봉이다 — 아직 이르다"
        )

    round_trip = costs.for_market(cycle.market).round_trip_pct
    return [_judge_one(item, candles, detect_index, cycle, round_trip) for item in cycle.proposals]


def _judge_one(
    proposal: Proposal,
    candles: Sequence[Candle],
    detect_index: int,
    cycle: Cycle,
    round_trip_pct: Decimal,
) -> Judgement:
    """참가자 하나를 판정한다.

    Args:
        proposal: 제안.
        candles: 전체 캔들.
        detect_index: 스냅샷 마지막 봉 번호.
        cycle: 회차.
        round_trip_pct: 왕복 비용률.

    Returns:
        판정.
    """
    blank = Judgement(
        participant=proposal.participant,
        kind=proposal.kind,
        stance=proposal.stance,
        entered=False,
        outcome=None,
        bars_to_entry=None,
        bars_held=None,
        planned_rr=None,
        risk_pct=None,
        gross_r=None,
        cost_r=None,
        net_r=None,
        conviction_pct=proposal.conviction_pct,
    )
    if not proposal.actionable:
        return blank

    setup = rebuild_setup(proposal)
    entry_index = _entry_index(candles, proposal, setup, detect_index)
    if entry_index is None:
        return blank

    verdict = judge(
        candles,
        setup,
        entry_index,
        max_hold_bars=cycle.hold_bars,
        expiry_index=entry_index + cycle.hold_bars,
    )
    if verdict.realized_r is None:
        # 기한까지 걸었는데 미결이면 캔들이 모자란 것이다 — `resolve_cycle` 이 이미
        # 길이를 확인했으므로 여기 오면 캔들에 구멍이 있다는 뜻이라 남겨서 보이게 한다.
        return blank

    with fixed_context():
        risk_pct = (setup.avg_entry - setup.stop_loss) / setup.avg_entry
        cost_r = round_trip_pct / risk_pct
        net_r = verdict.realized_r - cost_r

    return Judgement(
        participant=proposal.participant,
        kind=proposal.kind,
        stance=proposal.stance,
        entered=True,
        outcome=verdict.outcome,
        bars_to_entry=entry_index - detect_index,
        bars_held=verdict.bars_held,
        planned_rr=setup.rr_ratio,
        risk_pct=risk_pct,
        gross_r=verdict.realized_r,
        cost_r=cost_r,
        net_r=net_r,
        conviction_pct=proposal.conviction_pct,
    )


@dataclass(frozen=True, slots=True)
class Scorecard:
    """참가자 하나의 누적 성적 — **비교표의 한 행**이다.

    Attributes:
        participant: 참가자.
        kind: 종류.
        cycles: 참여 기회 수 (전체 회차).
        proposed: 계획을 낸 회차 수.
        abstained: 관망한 회차 수.
        failed: 무효응답 회차 수.
        entered: 실제로 체결된 회차 수 — **판정 표본**이다.
        followed/not_followed/expired: 결과별 건수.
        net_r_total: 순R 합.
        net_r_mean: 회차당 순R — 판정 기준값.
        gross_r_mean: 비용 전 평균. 비용이 얼마나 먹었는지 보이려고 함께 싣는다.
        median_rr: 계획 손익비 중앙값.
        median_risk_pct: 1R 크기 중앙값 — 비용이 큰 이유를 설명하는 칸이다.
        median_latency_ms: 지연 중앙값.
    """

    participant: str
    kind: ParticipantKind
    cycles: int
    proposed: int
    abstained: int
    failed: int
    entered: int
    followed: int
    not_followed: int
    expired: int
    net_r_total: Decimal
    net_r_mean: Decimal | None
    gross_r_mean: Decimal | None
    median_rr: Decimal | None
    median_risk_pct: Decimal | None
    median_latency_ms: int | None

    @property
    def win_rate(self) -> Decimal | None:
        """익절률 — 분모는 **체결된 회차**다. 0 건이면 None (절대 규칙 #8)."""
        if self.entered == 0:
            return None
        with fixed_context():
            return Decimal(self.followed) / Decimal(self.entered)

    @property
    def invalid_rate(self) -> Decimal | None:
        """무효응답률 — 관망은 분자가 아니다 (G-AI-4)."""
        if self.cycles == 0:
            return None
        with fixed_context():
            return Decimal(self.failed) / Decimal(self.cycles)

    @property
    def judgeable(self) -> bool:
        """§12.9 표본을 채웠는가. 미달이면 수치만 싣고 순위를 매기지 않는다."""
        return self.entered >= MIN_SAMPLE


def _median(values: Sequence[Decimal]) -> Decimal | None:
    """중앙값. 비면 None.

    Args:
        values: 값들.

    Returns:
        중앙값.
    """
    if not values:
        return None
    return Decimal(str(median(values)))


def tabulate(
    rounds: Sequence[Sequence[Judgement]],
    latencies: Sequence[Sequence[tuple[str, int]]] = (),
    conviction_floor: int | None = None,
) -> list[Scorecard]:
    """회차별 판정을 참가자별로 접는다.

    Args:
        rounds: 회차마다의 판정 목록.
        latencies: 회차마다의 `(참가자, 지연ms)`.
        conviction_floor: 주면 확신도가 이 값 미만인 판정을 **뺀다**. 부분집합 표를
            만들 때 쓴다 (`CONVICTION_CUT`).

    Returns:
        순R 평균 내림차순 성적표. 표본 미달은 뒤로 밀지 않는다 — 순위가 아니라 **판정
        가능 여부**로 갈리므로 `judgeable` 칸을 보고 읽어야 한다.

    Note:
        `cycles` 를 참가자별로 따로 세는 이유: 모델을 실험 도중 추가·제외하면 회차 수가
        서로 다르다. 전체 회차로 나누면 늦게 들어온 모델의 무효응답률이 낮게 나온다.

        ⚠️ `latencies` 는 `conviction_floor` 로 거르지 않는다. 지연은 **모델의 성질**이지
        그 회차 확신도의 함수가 아니므로, 부분집합 표에서도 전체 지연을 보는 쪽이 맞다.
    """
    order: list[str] = []
    buckets: dict[str, list[Judgement]] = {}
    for judgements in rounds:
        for item in judgements:
            if conviction_floor is not None and (
                item.conviction_pct is None or item.conviction_pct < conviction_floor
            ):
                continue
            if item.participant not in buckets:
                buckets[item.participant] = []
                order.append(item.participant)
            buckets[item.participant].append(item)

    latency_of: dict[str, list[int]] = {}
    for entries in latencies:
        for name, value in entries:
            latency_of.setdefault(name, []).append(value)

    cards = [_scorecard(name, buckets[name], latency_of.get(name, [])) for name in order]
    return sorted(
        cards,
        key=lambda card: (card.net_r_mean is None, -(card.net_r_mean or Decimal(0))),
    )


def _scorecard(name: str, items: Sequence[Judgement], latencies: Sequence[int]) -> Scorecard:
    """참가자 하나를 접는다.

    Args:
        name: 참가자.
        items: 그 참가자의 판정들.
        latencies: 지연들.

    Returns:
        성적표.
    """
    entered = [item for item in items if item.entered and item.net_r is not None]
    nets = [item.net_r for item in entered if item.net_r is not None]
    grosses = [item.gross_r for item in entered if item.gross_r is not None]
    total = sum(nets, Decimal(0))
    with fixed_context():
        net_mean = total / Decimal(len(nets)) if nets else None
        gross_mean = sum(grosses, Decimal(0)) / Decimal(len(grosses)) if grosses else None
    return Scorecard(
        participant=name,
        kind=items[0].kind,
        cycles=len(items),
        proposed=sum(1 for item in items if item.stance is Stance.PROPOSED),
        abstained=sum(1 for item in items if item.stance is Stance.ABSTAINED),
        failed=sum(1 for item in items if item.stance is Stance.FAILED),
        entered=len(entered),
        followed=sum(1 for item in entered if item.outcome is Outcome.FOLLOWED),
        not_followed=sum(1 for item in entered if item.outcome is Outcome.NOT_FOLLOWED),
        expired=sum(1 for item in entered if item.outcome is Outcome.EXPIRED),
        net_r_total=total,
        net_r_mean=net_mean,
        gross_r_mean=gross_mean,
        median_rr=_median([item.planned_rr for item in entered if item.planned_rr is not None]),
        median_risk_pct=_median([item.risk_pct for item in entered if item.risk_pct is not None]),
        median_latency_ms=int(median(latencies)) if latencies else None,
    )


def judgement_dict(item: Judgement) -> dict[str, object]:
    """판정 하나를 JSON 으로.

    Args:
        item: 판정.

    Returns:
        직렬화용 dict.
    """
    return {
        "participant": item.participant,
        "kind": item.kind.value,
        "stance": item.stance.value,
        "entered": item.entered,
        "outcome": None if item.outcome is None else item.outcome.value,
        "bars_to_entry": item.bars_to_entry,
        "bars_held": item.bars_held,
        "planned_rr": None if item.planned_rr is None else str(item.planned_rr),
        "risk_pct": None if item.risk_pct is None else str(item.risk_pct),
        "gross_r": None if item.gross_r is None else str(item.gross_r),
        "cost_r": None if item.cost_r is None else str(item.cost_r),
        "net_r": None if item.net_r is None else str(item.net_r),
        "conviction_pct": item.conviction_pct,
    }


def judgement_of(raw: dict[str, object]) -> Judgement:
    """직렬화된 판정을 되살린다.

    Args:
        raw: 직렬화된 판정.

    Returns:
        판정.
    """

    def price(key: str) -> Decimal | None:
        """None 을 허용하는 수 한 칸.

        Args:
            key: 열 이름.

        Returns:
            Decimal. 비어 있으면 None — 0 으로 꾸미면 "값이 없다" 가 사라진다.
        """
        value = raw.get(key)
        return None if value is None else Decimal(str(value))

    def count(key: str) -> int | None:
        """None 을 허용하는 정수 한 칸.

        Args:
            key: 열 이름.

        Returns:
            int. 비어 있으면 None.
        """
        value = raw.get(key)
        return None if value is None else int(str(value))

    outcome = raw.get("outcome")
    return Judgement(
        participant=str(raw["participant"]),
        kind=ParticipantKind(str(raw["kind"])),
        stance=Stance(str(raw["stance"])),
        entered=bool(raw.get("entered", False)),
        outcome=None if outcome is None else Outcome(str(outcome)),
        bars_to_entry=count("bars_to_entry"),
        bars_held=count("bars_held"),
        planned_rr=price("planned_rr"),
        risk_pct=price("risk_pct"),
        gross_r=price("gross_r"),
        cost_r=price("cost_r"),
        net_r=price("net_r"),
        conviction_pct=count("conviction_pct"),
    )
