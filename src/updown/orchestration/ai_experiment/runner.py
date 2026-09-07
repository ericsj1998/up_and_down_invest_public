"""라이브 실험의 두 루프 — **수집**과 **판정** (Phase 5 §5-5).

```
T+0    스냅샷 고정(캔들 해시) → 10종 동시 호출 → 제안 저장
       ↳ **같은 스냅샷**으로 우리 알고리즘도 돌려 저장 (같은 자에 놓기 위해)
T+보유 judge() 로 판정 → net_r 기록
```

## 왜 두 루프인가

판정은 24시간 뒤에야 가능하다. 한 프로세스가 기다리면 그 사이 죽었을 때 회차가 통째로
사라진다. 수집은 저장까지 하고 끝내고, 판정은 **원장을 훑어 익은 것만** 처리한다 —
그래서 재기동이 안전하고, 며칠 멈췄다 켜도 밀린 회차가 그대로 판정된다.

## 🔴 LLM 과 우리 알고리즘이 같은 마지막 봉을 본다

`analyze()` 가 고정한 스냅샷의 15m 마지막 봉을 기준으로, 탐지용 캔들을 **그 봉까지
잘라서** 넘긴다. 자르지 않으면 우리 알고리즘만 몇 초 더 최신 데이터를 보게 되고, 그
차이가 성적표에 우리 편으로 실린다.

## 프롬프트를 건드리지 않는다

탐지에는 400봉보다 긴 이력이 필요하지만 **프롬프트에 실리는 양을 늘리지 않는다.**
프롬프트가 바뀌면 G-AI-5 상 전 모델이 새 참가자가 되어 표본이 0 부터 다시 시작한다.
그래서 탐지용 캔들은 별도로 받는다.
"""

import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.detectors.base import SetupDetector
from updown.analysis.detectors.registry import SetupRegistry
from updown.analysis.detectors.rules import load_rules
from updown.analysis.evaluation.scan import DEFAULT_LOOKBACK_BARS
from updown.analysis.gates.trend_gate import TrendLookup
from updown.analysis.indicators.atr import atr as atr_series
from updown.analysis.trend.service import evaluate as trend_evaluate
from updown.common.costs import CostTable, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Bucket, Instrument, Timeframe
from updown.common.logging.setup import get_logger
from updown.llm.pool import PoolConfig
from updown.llm.port import LlmClient
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_analysis import (
    AnalysisRequest,
    AnalysisResult,
    ModelVerdict,
    analyze,
)
from updown.orchestration.ai_experiment.participants import (
    MARKET_TRIGGER,
    algorithm_proposal,
    baseline_proposal,
    detect_now,
)
from updown.orchestration.ai_experiment.record import (
    Cycle,
    CycleSource,
    Ledger,
    ParticipantKind,
    Proposal,
    Stance,
    make_run_id,
)
from updown.orchestration.ai_experiment.scoring import (
    ENTRY_WAIT_BARS,
    Judgement,
    NotMaturedError,
    judgement_dict,
    resolve_cycle,
)

_logger = get_logger("orchestration.ai_experiment")

JUDGE_TIMEFRAME = Timeframe.M15
"""채점 시간축. 4시간마다 16봉이 새로 생겨 회차끼리 겹치지 않는다 (§5-5)."""

GATE_TIMEFRAME = Timeframe.H1
"""추세 게이트 시간축 (§5.4-2). 15m 셋업의 상위 TF 다."""

DEFAULT_HOLD_BARS = 96
"""보유 기한 = 24시간 (15m x 96). 화면의 `하루 (주문부터 24시간)` 와 같은 값이다."""

HOLD_NOTE = "하루 (주문부터 24시간)"
"""프롬프트에 실리는 보유 기간 문구. ⛔ 바꾸면 전 모델이 새 참가자다 (G-AI-5)."""

DETECT_BARS = 900
"""탐지용으로 받는 15m 봉 수. 창 400 + 잘라낼 여유 + ATR 워밍업."""

GATE_BARS = 600
"""추세 색인용 1h 봉 수."""

RESOLVE_WARMUP_BARS = 64
"""판정 시 스냅샷 봉 **이전**으로 더 받는 봉 수.

지정가 참가자의 `RETEST_CONFIRM` 판정이 ATR 계열을 요구하는데(§4.3.2 ②), ATR14 는
워밍업 14봉이 필요하다. 넉넉히 64봉을 두되 그 이상은 받지 않는다 — 판정에 쓰이지 않는
과거를 매 주기 다시 받으면 회차 수만큼 낭비가 쌓인다.
"""

type ProgressFn = Callable[[str], None]

TRIGGERS_ENV = "UPDOWN_AI_TRIGGERS"
"""트리거 룰 id 를 쉼표로 좁힌다. 비면 스윙 버킷에 켜진 룰 전부 (T224)."""


def _note(on_progress: ProgressFn | None, message: str) -> None:
    """진행을 알린다.

    Args:
        on_progress: 콜백.
        message: 한 줄.
    """
    if on_progress:
        on_progress(message)


def active_detectors() -> list[SetupDetector]:
    """`enabled: true` 인 트리거 룰들.

    Returns:
        탐지기들.

    Raises:
        ValueError: 활성 룰이 하나도 없는 경우. 조용히 빈 목록으로 돌면 우리 알고리즘이
            **영원히 관망**으로 기록되고, 그 표는 "우리 룰은 아무것도 안 한다"는
            거짓말이 된다 (절대 규칙 #8).

    Note:
        레지스트리가 `enabled: false` 인 룰을 뺀다 — 과탐지로 꺼진 룰은 여기에도 안 온다.
        어느 룰을 트리거로 쓸지는 `UPDOWN_AI_TRIGGERS` 로 좁힌다 (T224 — 룰 이름을 코드에
        두지 않는다). 비면 스윙 버킷에 켜진 룰 전부다.
    """
    registry = SetupRegistry.from_plugins(load_rules())
    wanted = {item.strip() for item in os.environ.get(TRIGGERS_ENV, "").split(",") if item.strip()}
    found: list[SetupDetector] = [
        item.detector
        for item in registry.enabled_for(Bucket.SWING)
        if not wanted or item.rule_id in wanted
    ]
    if not found:
        raise ValueError("활성 트리거 룰이 없다 — config/rules/*.yml 의 enabled 를 확인하라")
    return found


def _llm_proposal(verdict: ModelVerdict, entry: Decimal) -> Proposal:
    """모델 결과를 제안으로.

    Args:
        verdict: 모델 결과.
        entry: 스냅샷 현재가 — 시장가 진입의 평단이다.

    Returns:
        제안. 실패는 `FAILED` 이며 **원문을 그대로 들고 간다** (§5-2 F-4).
    """
    if verdict.analysis is None:
        kind = verdict.failure_kind.value if verdict.failure_kind else "UNKNOWN"
        return Proposal(
            participant=verdict.model,
            kind=ParticipantKind.LLM,
            stance=Stance.FAILED,
            stop_loss=None,
            take_profit_first=None,
            take_profit_full=None,
            avg_entry=None,
            entry_price=None,
            trigger="",
            rule="",
            conviction_pct=None,
            latency_ms=verdict.latency_ms,
            detail=f"{kind}: {verdict.detail[:300]}",
            raw_text=verdict.raw_text,
        )
    plan = verdict.analysis.plan
    return Proposal(
        participant=verdict.model,
        kind=ParticipantKind.LLM,
        stance=Stance.PROPOSED,
        stop_loss=plan.stop_loss,
        # 🔴 절반 익절이 **1차 익절**이다 — 우리 셋업의 `tp_ladder[0]` 과 같은 자리.
        take_profit_first=plan.take_profit_half,
        take_profit_full=plan.take_profit_full,
        avg_entry=entry,
        entry_price=entry,
        trigger=MARKET_TRIGGER,
        rule="",
        conviction_pct=plan.conviction_pct,
        latency_ms=verdict.latency_ms,
        detail=verdict.analysis.trend.value,
        raw_text=verdict.raw_text,
    )


async def _detection_candles(
    instrument: Instrument,
    provider: MarketDataProvider,
    timeframe: Timeframe,
    bars: int,
    upto: datetime,
) -> list[Candle]:
    """탐지용 캔들을 받아 **스냅샷 시점까지 자른다**.

    Args:
        instrument: 종목.
        provider: 조회 경로 (절대 규칙 #0).
        timeframe: 시간축.
        bars: 받을 봉 수.
        upto: 이 시각 **이하**의 봉만 남긴다.

    Returns:
        잘린 캔들.
    """
    adapter = provider.adapter_for(instrument.market)
    now = datetime.now(UTC)
    got = await adapter.get_candles(instrument, timeframe, now - interval(timeframe) * bars, now)
    return [item for item in got if item.ts <= upto]


async def collect_once(
    instrument: Instrument,
    provider: MarketDataProvider,
    client: LlmClient,
    pool: PoolConfig,
    ledger: Ledger,
    hold_bars: int = DEFAULT_HOLD_BARS,
    on_progress: ProgressFn | None = None,
) -> Cycle:
    """회차 하나를 수집해 원장에 남긴다.

    Args:
        instrument: 대상.
        provider: 조회 경로.
        client: LLM 포트.
        pool: 모델 풀.
        ledger: 원장.
        hold_bars: 보유 기한(봉).
        on_progress: 진행 로그 콜백.

    Returns:
        저장된 회차.

    Raises:
        ValueError: 스냅샷에 채점 시간축 캔들이 없거나 ATR 을 만들 수 없는 경우.
        ExperimentError: 같은 회차가 이미 있는 경우 (`Ledger.save_cycle`).
    """
    result = await analyze(
        AnalysisRequest(instrument=instrument, models=(), hold_note=HOLD_NOTE),
        provider,
        client,
        pool,
        on_progress=on_progress,
    )
    return await save_result(
        result, instrument, provider, ledger, hold_bars, CycleSource.SCHEDULED, on_progress
    )


async def save_result(
    result: AnalysisResult,
    instrument: Instrument,
    provider: MarketDataProvider,
    ledger: Ledger,
    hold_bars: int = DEFAULT_HOLD_BARS,
    source: CycleSource = CycleSource.SCHEDULED,
    on_progress: ProgressFn | None = None,
) -> Cycle:
    """이미 끝난 분석을 **원장에 남긴다** — 우리 알고리즘·기준선을 같은 스냅샷에 얹어서.

    Args:
        result: 분석 결과.
        instrument: 대상.
        provider: 조회 경로.
        ledger: 원장.
        hold_bars: 보유 기한(봉).
        source: 스케줄러가 냈는가, 사람이 눌렀는가.
        on_progress: 진행 로그 콜백.

    Returns:
        저장된 회차.

    Raises:
        ValueError: 스냅샷에 채점 시간축 캔들이 없거나 ATR 을 만들 수 없는 경우.
        ExperimentError: 같은 회차가 이미 있는 경우.

    Note:
        수동 분석(화면의 `AI 차트 분석하기`)도 **이 함수를 그대로 탄다.** 저장 경로가
        갈라지면 회차 모양이 달라지고, 그러면 판정기가 한쪽만 읽게 된다 — 나중에
        "수동 기록은 왜 채점이 안 됐지" 를 디버깅하는 대신 애초에 같은 길로 보낸다.

        구분은 `source` 한 칸으로만 한다 (`Cycle` 의 Note — 표본 편향).
    """
    snapshot = result.snapshot
    judged = snapshot.frames.get(JUDGE_TIMEFRAME.value, [])
    if not judged:
        raise ValueError(f"스냅샷에 {JUDGE_TIMEFRAME.value} 캔들이 없다 — 채점 기준을 못 정한다")
    last_bar_ts = judged[-1].ts

    _note(on_progress, "우리 알고리즘 — 같은 스냅샷으로 탐지")
    started = time.perf_counter()
    candles = await _detection_candles(
        instrument, provider, JUDGE_TIMEFRAME, DETECT_BARS, last_bar_ts
    )
    if len(candles) < DEFAULT_LOOKBACK_BARS:
        raise ValueError(f"탐지 창 {DEFAULT_LOOKBACK_BARS}봉이 필요한데 {len(candles)}봉만 받았다")
    gate_candles = await _detection_candles(
        instrument, provider, GATE_TIMEFRAME, GATE_BARS, last_bar_ts
    )
    lookup: TrendLookup | None = None
    if gate_candles:
        history = trend_evaluate(instrument, GATE_TIMEFRAME, gate_candles)
        lookup = TrendLookup.build(gate_candles, history, GATE_TIMEFRAME)

    setups, outcome = detect_now(candles, instrument, JUDGE_TIMEFRAME, active_detectors(), lookup)
    algorithm = algorithm_proposal(setups, outcome, int((time.perf_counter() - started) * 1000))
    _note(on_progress, f"우리 알고리즘 — {algorithm.stance.value} · {algorithm.detail}")

    series = atr_series(
        [item.high for item in candles],
        [item.low for item in candles],
        [item.close for item in candles],
    )
    atr = series[-1]
    if atr is None or atr <= 0:
        raise ValueError("ATR14 를 만들 수 없다 — 기준선을 세울 수 없다")

    proposals = [_llm_proposal(item, snapshot.entry) for item in result.verdicts]
    proposals.append(algorithm)
    proposals.append(baseline_proposal(snapshot.entry, atr))

    cycle = Cycle(
        run_id=make_run_id(instrument.symbol, snapshot.taken_at),
        symbol=instrument.symbol,
        market=instrument.market,
        timeframe=JUDGE_TIMEFRAME,
        taken_at=snapshot.taken_at,
        digest=snapshot.digest,
        entry=snapshot.entry,
        last_bar_ts=last_bar_ts,
        atr=atr,
        hold_bars=hold_bars,
        prompt_version=result.prompt_version,
        proposals=tuple(proposals),
        source=source,
    )
    ledger.save_cycle(cycle)
    _logger.info(
        "ai_experiment_collected",
        payload={
            "run_id": cycle.run_id,
            "digest": cycle.digest,
            "proposals": len(proposals),
            "invalid_rate": str(cycle.invalid_rate),
            "source": source.value,
        },
    )
    _note(on_progress, f"원장 저장 — {cycle.run_id} · 참가자 {len(proposals)}")
    return cycle


def matures_at(cycle: Cycle) -> datetime:
    """이 회차를 판정할 수 있게 되는 시각.

    Args:
        cycle: 회차.

    Returns:
        UTC 시각.

    Note:
        체결 대기 상한 + 보유 기한 + 여유 1봉이다. 시장가 참가자는 24시간이면 끝나지만
        지정가 참가자(우리 셋업)는 최대 4시간을 더 기다리므로, 한 번에 판정하려면 긴
        쪽에 맞춰야 한다 — 원장이 불변이라 두 번 나눠 쓸 수 없다 (`Ledger.save_verdict`).
    """
    bars = ENTRY_WAIT_BARS + cycle.hold_bars + 1
    return cycle.last_bar_ts + interval(cycle.timeframe) * bars


async def resolve_due(
    provider: MarketDataProvider,
    ledger: Ledger,
    instrument_of: Callable[[Cycle], Instrument],
    costs: CostTable | None = None,
    now: datetime | None = None,
    on_progress: ProgressFn | None = None,
) -> list[str]:
    """익은 회차를 모두 판정한다.

    Args:
        provider: 조회 경로.
        ledger: 원장.
        instrument_of: 회차에서 종목을 만드는 함수.
        costs: 비용 테이블. None 이면 설정에서 읽는다.
        now: 기준 시각. 테스트에서 주입한다.
        on_progress: 진행 로그 콜백.

    Returns:
        판정한 회차 id 들.
    """
    table = costs or load_cost_table()
    moment = now or datetime.now(UTC)
    done: list[str] = []

    for cycle in ledger.cycles():
        if ledger.has_verdict(cycle.run_id):
            continue
        ready = matures_at(cycle)
        if moment < ready:
            _note(on_progress, f"{cycle.run_id} — {ready.isoformat()} 이후 판정")
            continue

        instrument = instrument_of(cycle)
        adapter = provider.adapter_for(cycle.market)
        candles = await adapter.get_candles(
            instrument,
            cycle.timeframe,
            cycle.last_bar_ts - interval(cycle.timeframe) * RESOLVE_WARMUP_BARS,
            moment,
        )
        try:
            judgements = resolve_cycle(cycle, candles, table)
        except NotMaturedError as exc:
            # 시간은 됐는데 봉이 모자라다 — 거래소 지연이나 결측이다. 다음 주기에 다시.
            _note(on_progress, f"⏳ {cycle.run_id} — {exc}")
            continue

        ledger.save_verdict(cycle.run_id, _verdict_payload(cycle, judgements, moment))
        done.append(cycle.run_id)
        _note(on_progress, f"✅ {cycle.run_id} 판정 — 참가자 {len(judgements)}")

    return done


def _verdict_payload(
    cycle: Cycle, judgements: Sequence[Judgement], judged_at: datetime
) -> dict[str, object]:
    """판정 파일 본문.

    Args:
        cycle: 회차.
        judgements: 판정들.
        judged_at: 판정 시각.

    Returns:
        직렬화용 dict.
    """
    return {
        "run_id": cycle.run_id,
        "symbol": cycle.symbol,
        "taken_at": cycle.taken_at.isoformat(),
        "judged_at": judged_at.isoformat(),
        "hold_bars": cycle.hold_bars,
        "entry_wait_bars": ENTRY_WAIT_BARS,
        "prompt_version": cycle.prompt_version,
        "judgements": [judgement_dict(item) for item in judgements],
    }


def next_slot(now: datetime, every: timedelta) -> datetime:
    """다음 발사 시각 — **벽시계 격자에 맞춘다**.

    Args:
        now: 현재 (UTC).
        every: 주기.

    Returns:
        다음 시각.

    Note:
        `now + every` 로 두면 재기동할 때마다 격자가 밀려 회차 간격이 제멋대로가 된다.
        4시간 주기면 00·04·08·12·16·20시 UTC 에 고정되므로 며칠 뒤에도 회차 시각을
        예측할 수 있고, 중단 구간이 표에서 눈에 띈다.
    """
    seconds = int(every.total_seconds())
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = int((now - epoch).total_seconds())
    return epoch + timedelta(seconds=(elapsed // seconds + 1) * seconds)
