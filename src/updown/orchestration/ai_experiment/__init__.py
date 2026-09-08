"""LLM 대 우리 알고리즘 **라이브 전진 비교 실험** (Phase 5 §5-5).

이 트랙의 진짜 목표다. 백테스트가 아니라 라이브인 이유는 하나뿐이다:

> LLM 학습 데이터 오염을 **막을 방법이 없다**. 2025년 BTC 차트는 모델이 이미 봤을 수
> 있고, 그러면 "분석을 잘한 것"과 "기억한 것"이 구분되지 않는다. 오늘 이후는 어떤
> 모델도 못 봤다.

대가는 속도다 — 4시간마다 1회, 30건까지 5일 (§5-5 표본 축적 설계).
"""

from updown.orchestration.ai_experiment.participants import (
    ALGORITHM_NAME,
    BASELINE_NAME,
    algorithm_proposal,
    baseline_proposal,
    detect_now,
)
from updown.orchestration.ai_experiment.record import (
    Cycle,
    CycleSource,
    ExperimentError,
    Ledger,
    ParticipantKind,
    Proposal,
    Stance,
    make_run_id,
)
from updown.orchestration.ai_experiment.runner import (
    DEFAULT_HOLD_BARS,
    DETECT_BARS,
    GATE_TIMEFRAME,
    JUDGE_TIMEFRAME,
    active_detectors,
    collect_once,
    matures_at,
    next_slot,
    resolve_due,
    save_result,
)
from updown.orchestration.ai_experiment.scoring import (
    CONVICTION_CUT,
    ENTRY_WAIT_BARS,
    MIN_SAMPLE,
    Judgement,
    NotMaturedError,
    Scorecard,
    judgement_of,
    rebuild_setup,
    resolve_cycle,
    tabulate,
)

__all__ = [
    "ALGORITHM_NAME",
    "BASELINE_NAME",
    "CONVICTION_CUT",
    "DEFAULT_HOLD_BARS",
    "DETECT_BARS",
    "ENTRY_WAIT_BARS",
    "GATE_TIMEFRAME",
    "JUDGE_TIMEFRAME",
    "MIN_SAMPLE",
    "Cycle",
    "CycleSource",
    "ExperimentError",
    "Judgement",
    "Ledger",
    "NotMaturedError",
    "ParticipantKind",
    "Proposal",
    "Scorecard",
    "Stance",
    "active_detectors",
    "algorithm_proposal",
    "baseline_proposal",
    "collect_once",
    "detect_now",
    "judgement_of",
    "make_run_id",
    "matures_at",
    "next_slot",
    "rebuild_setup",
    "resolve_cycle",
    "resolve_due",
    "save_result",
    "tabulate",
]
