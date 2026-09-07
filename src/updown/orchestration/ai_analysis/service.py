"""AI 차트 분석 실행 (Phase 5 §5-3 · §5-6).

## 실시간이다 — 백필을 쓰지 않는다

요구 사항이다. 조회는 `MarketDataProvider` 로만 한다 (절대 규칙 #0). 걸린 시간을
**단계별로 재서** 결과에 싣는다 — 요청서가 명시한 항목이고, 체감 지연의 원인을
숫자로 봐야 캐시를 어디에 둘지 정할 수 있다.

## 🔴 스냅샷을 해시로 고정한다

10종을 동시에 부르는데 그 사이 마지막 봉이 갱신되면, 모델마다 다른 캔들을 본 것이 된다.
그러면 **모델이 아니라 시장을 비교**하게 된다 (§5-5). 한 번 받아 고정하고 해시를 남긴다.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.logging.setup import get_logger
from updown.llm.pool import PoolConfig, fan_out
from updown.llm.port import FailureKind, LlmClient, LlmFailure, LlmSuccess
from updown.llm.schema import ChartAnalysis, SchemaError, parse_analysis
from updown.marketdata.ingest.calendar_span import CalendarSpan, SpanBar, aggregate_calendar
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_analysis.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)

_logger = get_logger("orchestration.ai_analysis")

MS = 1000

#: 분석에 싣는 타임프레임들. 요청서가 지정한 7종이며 주·월봉은 일봉에서 합성한다.
INTRADAY_FRAMES: tuple[Timeframe, ...] = (
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)

#: 타임프레임별로 받을 봉 수. 지표 창(400봉)이 아니라 **프롬프트에 실을 양**이다.
FETCH_BARS = 400

type ProgressFn = Callable[[str], None]
"""진행 로그 콜백 — 화면이 SSE 로 흘린다 (§5-6)."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """한 시점에 고정된 캔들 묶음.

    Attributes:
        instrument: 대상 종목.
        taken_at: 스냅샷 시각 (UTC).
        frames: 타임프레임 이름 → 캔들.
        weekly/monthly: 일봉에서 합성한 상위 봉.
        entry: 현재가 = 가장 짧은 프레임의 마지막 종가.
        digest: 캔들 내용 해시. **10종이 같은 것을 봤다는 증거**다.
        timings_ms: 단계별 소요 시간.
    """

    instrument: Instrument
    taken_at: datetime
    frames: dict[str, list[Candle]]
    weekly: list[SpanBar]
    monthly: list[SpanBar]
    entry: Decimal
    digest: str
    timings_ms: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True, slots=True)
class ModelVerdict:
    """모델 하나의 결과 — 성공이든 실패든 **기록된다**.

    Attributes:
        model: 모델 id.
        analysis: 파싱·검증을 통과한 분석. 실패면 None.
        failure_kind: 실패 분류. 성공이면 None.
        detail: 실패 사유.
        latency_ms: 지연.
        raw_text: 모델 원문. 파서를 고친 뒤 다시 해석할 수 있어야 한다 (§5-2 F-4).
    """

    model: str
    analysis: ChartAnalysis | None
    failure_kind: FailureKind | None
    detail: str
    latency_ms: int
    raw_text: str


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """분석 요청.

    Attributes:
        instrument: 대상.
        models: 부를 모델 id 들. 비면 풀 전체.
        hold_note: 주문 유지 기간 설명 — 프롬프트에 그대로 들어간다.
    """

    instrument: Instrument
    models: tuple[str, ...] = ()
    hold_note: str = "상관 없음"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """분석 한 회 전체.

    Attributes:
        snapshot: 고정된 입력.
        verdicts: 모델별 결과.
        prompt_version: 프롬프트 판 번호 (§G-AI-5).
        total_ms: 전체 소요.
    """

    snapshot: Snapshot
    verdicts: tuple[ModelVerdict, ...]
    prompt_version: str
    total_ms: int

    @property
    def invalid_rate(self) -> Decimal:
        """무효 응답 비율 — **비교표에서 숨기지 않는 칸**이다 (G-AI-4)."""
        if not self.verdicts:
            return Decimal(0)
        bad = sum(1 for item in self.verdicts if item.analysis is None)
        return Decimal(bad) / Decimal(len(self.verdicts))


def _digest(frames: dict[str, list[Candle]]) -> str:
    """캔들 묶음의 해시.

    Args:
        frames: 타임프레임 → 캔들.

    Returns:
        16자리 hex.

    Note:
        마지막 봉의 시각·종가만으로도 충분하다 — 그 둘이 같으면 같은 스냅샷이다.
        전체를 해싱하면 400봉 x 7프레임을 매번 도는 비용이 붙는다.
    """
    parts = [
        f"{name}:{candles[-1].ts.isoformat()}:{candles[-1].close}"
        for name, candles in sorted(frames.items())
        if candles
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


async def fetch_snapshot(
    instrument: Instrument,
    provider: MarketDataProvider,
    on_progress: ProgressFn | None = None,
) -> Snapshot:
    """실시간 멀티 타임프레임 스냅샷을 받는다.

    Args:
        instrument: 대상.
        provider: 조회 경로 (절대 규칙 #0).
        on_progress: 진행 로그 콜백.

    Returns:
        고정된 스냅샷.

    Raises:
        ValueError: 캔들을 하나도 받지 못한 경우 — 빈 분석을 만드는 것보다 멈춘다.
    """
    started = time.perf_counter()
    timings: dict[str, int] = {}
    adapter = provider.adapter_for(instrument.market)
    now = datetime.now(UTC)
    frames: dict[str, list[Candle]] = {}

    def note(message: str) -> None:
        """진행을 알린다.

        Args:
            message: 사람이 읽을 한 줄. 콜백이 없으면 버린다 (시험·배치 실행).
        """
        if on_progress:
            on_progress(message)

    for timeframe in INTRADAY_FRAMES:
        step = time.perf_counter()
        # 필요한 봉 수만 받는다. 수년치를 받으면 느려지고, 분석에 쓰이지도 않는다.
        span = _lookback(timeframe)
        candles = await adapter.get_candles(instrument, timeframe, now - span, now)
        frames[timeframe.value] = candles
        timings[timeframe.value] = int((time.perf_counter() - step) * MS)
        note(f"{timeframe.value} {len(candles)}봉 · {timings[timeframe.value]}ms")

    daily = frames.get(Timeframe.D1.value, [])
    if not daily:
        raise ValueError("일봉을 받지 못했다 — 주·월봉을 만들 수 없다")

    step = time.perf_counter()
    weekly = aggregate_calendar(daily, CalendarSpan.WEEK, instrument.market)
    monthly = aggregate_calendar(daily, CalendarSpan.MONTH, instrument.market)
    timings["calendar"] = int((time.perf_counter() - step) * MS)
    note(f"주봉 {len(weekly)} · 월봉 {len(monthly)} 합성 · {timings['calendar']}ms")

    shortest = frames.get(Timeframe.M5.value) or daily
    if not shortest:
        raise ValueError("캔들이 비었다 — 현재가를 정할 수 없다")

    timings["total_fetch"] = int((time.perf_counter() - started) * MS)
    return Snapshot(
        instrument=instrument,
        taken_at=now,
        frames=frames,
        weekly=weekly,
        monthly=monthly,
        entry=shortest[-1].close,
        digest=_digest(frames),
        timings_ms=timings,
    )


def _lookback(timeframe: Timeframe) -> timedelta:
    """타임프레임별로 거슬러 갈 기간.

    Args:
        timeframe: 시간축.

    Returns:
        기간. 일봉만 길다 — 주·월봉의 재료라서 수년이 필요하다.
    """
    if timeframe is Timeframe.D1:
        # 월봉 60개를 만들려면 5년이 필요하다. 일봉은 네이티브라 싸다 (실측 3.3초/10년).
        return timedelta(days=365 * 5)
    minutes = {
        Timeframe.M5: 5,
        Timeframe.M15: 15,
        Timeframe.H1: 60,
        Timeframe.H4: 240,
    }[timeframe]
    return timedelta(minutes=minutes * FETCH_BARS)


async def analyze(
    request: AnalysisRequest,
    provider: MarketDataProvider,
    client: LlmClient,
    config: PoolConfig,
    on_progress: ProgressFn | None = None,
) -> AnalysisResult:
    """스냅샷을 받아 모델들에 던지고 결과를 모은다.

    Args:
        request: 요청.
        provider: 조회 경로.
        client: LLM 포트.
        config: 팬아웃 설정.
        on_progress: 진행 로그 콜백.

    Returns:
        회차 결과. **실패한 모델도 들어 있다** (G-AI-4).
    """
    started = time.perf_counter()

    def note(message: str) -> None:
        """진행을 알린다.

        Args:
            message: 사람이 읽을 한 줄. 콜백이 없으면 버린다 (시험·배치 실행).
        """
        if on_progress:
            on_progress(message)

    snapshot = await fetch_snapshot(request.instrument, provider, on_progress)
    note(f"스냅샷 고정 · digest={snapshot.digest} · 현재가 {snapshot.entry}")

    user_prompt = build_user_prompt(snapshot.frames, snapshot.entry, request.hold_note)
    chosen = request.models or tuple(spec.id for spec in config.models)
    note(f"LLM {len(chosen)}종 동시 호출")

    outcomes = await fan_out(client, config, SYSTEM_PROMPT, user_prompt, chosen)

    verdicts: list[ModelVerdict] = []
    for outcome in outcomes:
        if isinstance(outcome, LlmFailure):
            note(f"⛔ {outcome.model} — {outcome.kind.value}")
            verdicts.append(
                ModelVerdict(
                    model=outcome.model,
                    analysis=None,
                    failure_kind=outcome.kind,
                    detail=outcome.detail,
                    latency_ms=outcome.latency_ms,
                    raw_text="",
                )
            )
            continue
        success: LlmSuccess = outcome
        try:
            analysis = parse_analysis(success.text, snapshot.entry)
        except SchemaError as exc:
            # ⛔ 고쳐서 살리지 않는다 — "형식을 못 지킨다"가 비교의 핵심 정보다 (§5-4).
            note(f"⛔ {success.model} — 스키마 위반")
            verdicts.append(
                ModelVerdict(
                    model=success.model,
                    analysis=None,
                    failure_kind=FailureKind.INVALID_SCHEMA,
                    detail=str(exc),
                    latency_ms=success.latency_ms,
                    raw_text=success.text,
                )
            )
            continue
        note(f"✅ {success.model} · {success.latency_ms}ms")
        verdicts.append(
            ModelVerdict(
                model=success.model,
                analysis=analysis,
                failure_kind=None,
                detail="",
                latency_ms=success.latency_ms,
                raw_text=success.text,
            )
        )

    total = int((time.perf_counter() - started) * MS)
    _logger.info(
        "ai_analysis_done",
        payload={
            "symbol": request.instrument.symbol,
            "digest": snapshot.digest,
            "models": len(chosen),
            "invalid": sum(1 for item in verdicts if item.analysis is None),
            "total_ms": total,
        },
    )
    return AnalysisResult(
        snapshot=snapshot,
        verdicts=tuple(verdicts),
        prompt_version=PROMPT_VERSION,
        total_ms=total,
    )
