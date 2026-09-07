"""LLM 이 아닌 두 참가자 — **우리 알고리즘**과 **동전 던지기** (Phase 5 §5-5).

## 왜 기준선이 있어야 하는가

> 🔴 기준선을 반드시 넣는다. §1-0s 에서 "코인 +50~98%"가 전략 성과가 아니라 **대칭
> 동전 던지기**였던 적이 있다. 기준선이 없으면 같은 착각을 반복한다.

10종 중 1등이 +0.4R 이라 해도, 아무 근거 없이 매 회차 사는 것이 +0.4R 이면 그 모델은
아무것도 한 것이 없다. 그 비교가 가능하려면 기준선이 **같은 스냅샷·같은 판정기·같은
비용**을 통과해야 한다.

## 기준선의 구성 — 측정 전에 선언한다 (§5.6.2)

| | 값 | 왜 |
|---|---|---|
| 진입 | 스냅샷 현재가에 **매 회차 시장가** | 관망하지 않는다. "언제나 산다"가 바닥값이다 |
| 손절 | `entry - 1.0 x ATR14(15m)` | 변동성 1단위. 종목·시점에 따라 절대폭이 달라도 R 은 같다 |
| 익절 | `entry + 1.0 x ATR14(15m)` | **대칭**이라 RR 1.00 — 기하적으로 승률 기대 50% |

⚠️ 이 값들은 결과를 보고 고른 것이 아니다. RR 1.00 대칭은 "동전 던지기"라는 이름이
요구하는 유일한 구성이며, 다른 배수를 골랐다면 그 순간 기준선이 하나의 **전략**이 된다.

## 우리 알고리즘은 관망한다 — 그것이 결과다

LLM 은 물으면 반드시 계획을 낸다. 우리 탐지기는 대부분의 봉에서 아무것도 내지 않는다.
그 차이를 무효응답으로 적으면 거짓말이 되므로 `Stance.ABSTAINED` 로 따로 센다
(`record.py`). **참여율 자체가 비교 항목**이다 — 자주 틀리는 참가자보다 드물게 맞는
참가자가 나을 수 있고, 그 판단은 회차당 기대값이 아니라 기간당 누적으로 해야 한다.
"""

from collections.abc import Sequence
from decimal import Decimal

from updown.analysis.detectors.base import SetupDetector
from updown.analysis.evaluation.scan import DEFAULT_LOOKBACK_BARS, build_context
from updown.analysis.gates.trend_gate import TrendGateOutcome, TrendLookup
from updown.analysis.gates.trend_gate import judge as judge_trend
from updown.analysis.structures.params import StructureParams
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.setup import TradeSetup
from updown.orchestration.ai_experiment.record import (
    ParticipantKind,
    Proposal,
    Stance,
)

ALGORITHM_NAME = "우리-알고리즘"
BASELINE_NAME = "동전던지기-기준선"

MARKET_TRIGGER = "MARKET"
"""시장가 즉시 진입 — LLM 과 기준선이 쓴다. `EntryTrigger` 에 없는 이유는 그 열거형이
**탐지기가 내놓는 조건부 진입**의 목록이기 때문이다. 조건 없이 지금 사는 것은 트리거가
아니라 트리거의 부재다."""

BASELINE_ATR_MULTIPLE = Decimal(1)
"""기준선의 손절·익절 거리 (ATR 배수). ⛔ 성과로 조정 금지 — 대칭이 정의다."""

PERCENT = Decimal(100)


def baseline_proposal(entry: Decimal, atr: Decimal) -> Proposal:
    """동전 던지기 기준선의 제안 (G-AI-2).

    Args:
        entry: 스냅샷 현재가.
        atr: 채점 시간축의 ATR14.

    Returns:
        RR 1.00 대칭 제안. 언제나 `PROPOSED` 다 — 관망하지 않는 것이 바닥값의 정의다.

    Raises:
        ValueError: ATR 이 0 이하라 손절가가 진입가 이상이 되는 경우. 조용히 넘기면
            "손절 없는 거래"가 기준선으로 들어간다 (절대 규칙 #8).
    """
    if atr <= 0:
        raise ValueError(f"ATR 이 {atr} 다 — 기준선의 손절폭을 만들 수 없다")
    distance = atr * BASELINE_ATR_MULTIPLE
    return Proposal(
        participant=BASELINE_NAME,
        kind=ParticipantKind.BASELINE,
        stance=Stance.PROPOSED,
        stop_loss=entry - distance,
        take_profit_first=entry + distance,
        take_profit_full=entry + distance,
        avg_entry=entry,
        entry_price=entry,
        trigger=MARKET_TRIGGER,
        rule="",
        conviction_pct=None,
        latency_ms=0,
        detail=f"ATR14 {atr} x {BASELINE_ATR_MULTIPLE} 대칭 (RR 1.00)",
        raw_text="",
    )


def detect_now(
    candles: Sequence[Candle],
    instrument: Instrument,
    timeframe: Timeframe,
    detectors: Sequence[SetupDetector],
    trend_lookup: TrendLookup | None = None,
    structure_params: StructureParams | None = None,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
) -> tuple[list[TradeSetup], TrendGateOutcome]:
    """**마지막 봉 시점**에 탐지기들이 내놓는 셋업을 모은다.

    Args:
        candles: 채점 시간축 캔들 (`ts` 오름차순). 마지막 봉이 현재다.
        instrument: 종목.
        timeframe: 시간축.
        detectors: 돌릴 탐지기들.
        trend_lookup: 상위 TF 추세 색인. 주면 §5.4-2 게이트가 걸린다.
        structure_params: 구조물 파라미터.
        lookback_bars: 탐지 창.

    Returns:
        `(통과한 셋업들, 게이트 판정)`. 게이트에 막히면 셋업은 빈 목록이다.

    Raises:
        ValueError: 창을 채울 만큼 캔들이 없는 경우. 짧은 창으로 탐지하면 같은 룰이
            다른 것을 보게 되어 운영과 측정이 갈라진다.

    Note:
        `scan.collect` 를 쓰지 않는 이유는 그 함수가 **마지막 봉을 일부러 뺀다**는 데
        있다 (`range(lookback-1, len-1)`). 과거 측정에서는 진입할 다음 봉이 필요해서
        맞지만, 라이브에서 우리가 알고 싶은 것은 정확히 그 마지막 봉이다.

        게이트를 여기서 **실제로 거는** 이유: 이 참가자는 "우리가 지금 실제로 매매할
        것"이라야 한다. 측정에서는 게이트 효과를 재려고 `apply_gate=False` 로 두지만,
        비교표의 우리 행이 게이트 없는 판이면 우리가 하지 않을 매매를 우리 성적으로
        적는 셈이다.
    """
    if len(candles) < lookback_bars:
        raise ValueError(f"탐지 창 {lookback_bars}봉이 필요한데 {len(candles)}봉뿐이다")

    window = list(candles[-lookback_bars:])
    context = build_context(
        window, instrument, timeframe, structure_params or StructureParams(), trend_lookup
    )
    state = None if trend_lookup is None else trend_lookup.at(context.as_of)
    outcome = TrendGateOutcome.ALLOWED if trend_lookup is None else judge_trend(state)
    if not outcome.is_allowed:
        return [], outcome

    found: list[TradeSetup] = []
    for detector in detectors:
        found.extend(detector.detect(context))
    return found, outcome


def pick(setups: Sequence[TradeSetup]) -> TradeSetup | None:
    """여러 셋업이 동시에 잡혔을 때 하나를 고른다.

    Args:
        setups: 후보들.

    Returns:
        고른 셋업. 비면 None.

    Note:
        🔴 **규칙을 측정 전에 못박는다** (§5.6.2): `confidence` 내림차순, 동률이면
        `rule_version` 사전순. 뒤 조건이 있어야 결정론이 성립한다 (절대 규칙 #5) —
        confidence 가 같을 때 dict 순서에 기대면 같은 입력이 다른 답을 낼 수 있다.

        ⛔ "RR 이 높은 것"이나 "손절이 가까운 것"을 고르지 않는다. 그런 기준은 곧
        성과를 겨냥한 선택이 되고, 그 선택 자체가 측정되지 않은 새 규칙이다.
    """
    if not setups:
        return None
    return sorted(setups, key=lambda item: (-item.confidence, item.rule_version))[0]


def algorithm_proposal(
    setups: Sequence[TradeSetup],
    outcome: TrendGateOutcome,
    latency_ms: int = 0,
) -> Proposal:
    """우리 알고리즘의 제안 — 셋업이 없으면 **관망**이다.

    Args:
        setups: 탐지된 셋업들.
        outcome: 추세 게이트 판정. 관망 사유를 적기 위해 받는다.
        latency_ms: 탐지에 걸린 시간.

    Returns:
        제안 또는 관망. **실패가 아니다** (`record.py` 모듈 docstring).

    Note:
        `take_profit_first` 는 `tp_ladder[0]` 이다 — LLM 의 `take_profit_half` 와 같은
        자리(1차 익절)이며, 그래서 두 참가자가 같은 것을 목표로 채점된다.
    """
    chosen = pick(setups)
    if chosen is None:
        reason = "셋업 없음" if outcome.is_allowed else f"추세 게이트 기각 ({outcome.value})"
        return Proposal(
            participant=ALGORITHM_NAME,
            kind=ParticipantKind.ALGORITHM,
            stance=Stance.ABSTAINED,
            stop_loss=None,
            take_profit_first=None,
            take_profit_full=None,
            avg_entry=None,
            entry_price=None,
            trigger="",
            rule="",
            conviction_pct=None,
            latency_ms=latency_ms,
            detail=reason,
            raw_text="",
        )

    ladder = chosen.tp_ladder
    return Proposal(
        participant=ALGORITHM_NAME,
        kind=ParticipantKind.ALGORITHM,
        stance=Stance.PROPOSED,
        stop_loss=chosen.stop_loss,
        take_profit_first=ladder[0].price,
        take_profit_full=ladder[-1].price,
        avg_entry=chosen.avg_entry,
        entry_price=chosen.entry_plan[0].price,
        trigger=chosen.entry_trigger.value,
        rule=chosen.rule_version,
        conviction_pct=int(Decimal(str(chosen.confidence)) * PERCENT),
        latency_ms=latency_ms,
        detail=f"{chosen.setup_type} · RR {chosen.rr_ratio}",
        raw_text="",
    )
