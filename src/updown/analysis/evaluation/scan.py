"""과거 구간을 걸어가며 셋업을 모으고 이행 여부를 판정한다 (spec §5.6.6).

## 근사하지 않는다 — 실제 탐지기를 봉마다 돌린다

빠른 길이 있었다. 감쌈·급등·유동성 조건을 한 번에 훑어 후보를 뽑고 앞으로 걸어가는
단일 패스면 수십 배 빠르다. **쓰지 않았다.** 그 방식은 탐지기와 *다른 코드*가 되고,
측정한 대상이 실제로 도는 룰과 같다는 보장이 사라진다. 이행률은 룰의 채택 근거가 되므로
(§5.6.7) 그 보장이 없으면 측정 자체가 무의미하다.

대신 봉마다 `MarketContext` 를 조립해 `detector.detect()` 를 그대로 호출한다. 실측 비용은
5m 1년치(106,125봉)에서 **약 6분**이다 — 지표 2.3ms + 탐지 1.3ms per 봉. 리포트는 자주
도는 물건이 아니므로 정확성을 택했다.

## 미래를 보는 곳과 보지 않는 곳을 갈랐다

| 단계 | 보이는 것 |
|------|----------|
| **탐지** | `AsOfSequence` 로 감싼 400봉 창 — 미래 봉 접근 시 `LookaheadError` |
| **판정** | 전체 캔들 — **일부러 미래를 본다** (이행 여부는 미래에만 있다) |

두 단계가 같은 함수 안에 있으므로 경계가 흐려지기 쉽다. 탐지에 넘기는 것은 언제나 창
슬라이스이며, 전체 리스트는 `judge()` 에만 넘어간다.

## 같은 박스를 여러 번 세지 않는다

오더블록은 가격이 박스 위에 있는 동안 **매 봉 다시 탐지된다.** 그대로 세면 한 박스가
수십 건이 되어 이행률이 "오래 살아남은 박스"의 성질로 오염된다.

셋업의 정체성을 **진입가들 + 손절가**로 잡는다 (`setup_identity`). 박스가 다르면 이 값들이
다르므로 셋업 종류를 몰라도 구분되며, P1-6·P1-7 의 다른 탐지기에도 그대로 쓰인다.

## 어느 시점의 셋업으로 판정하는가 — **최초 탐지본**

같은 박스라도 봉이 지나면 1차 익절가(급등 고점)가 올라갈 수 있다. 최초 탐지본을 쓰는
이유는 측정 질문이 "**그 탐지가** 예측대로 움직였나"이기 때문이다. 진입 직전 값을 쓰면
같은 탐지의 판정이 대기 기간에 따라 달라지고, 대기가 길수록 목표가 높아져 이행률이
내려간다 — 측정이 룰이 아니라 대기 시간을 재게 된다.

## 진입을 언제까지 기다리는가 — 탐지기가 정한다

새 파라미터를 만들지 않았다. 탐지기가 그 셋업을 **마지막으로 내놓은 봉**까지가 유효
구간이다 (오더블록이면 `max_age_bars` 만료·무효화가 여기에 반영된다). 그 다음 봉까지
진입이 없으면 `미진입`이다.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from updown.analysis.context.guard import AsOfSequence
from updown.analysis.detectors.base import MarketContext, SetupDetector
from updown.analysis.evaluation.evidence import EvidenceLedger
from updown.analysis.evaluation.flags import FlagEvaluator, evaluate_flags
from updown.analysis.evaluation.follow_through import (
    DEFAULT_MAX_HOLD_BARS,
    FollowThroughRecord,
    FollowThroughStats,
    entry_level,
    find_entry,
    judge,
    summarize,
)
from updown.analysis.gates.trend_gate import TrendGateOutcome, TrendLookup
from updown.analysis.gates.trend_gate import judge as judge_trend
from updown.analysis.indicators import snapshot
from updown.analysis.indicators.seasonal_volume import VolumeBaseline
from updown.analysis.structures.bundle import compute_bundle
from updown.analysis.structures.params import StructureParams
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.reports import Indicators, TrendDirection
from updown.common.domain.setup import TradeSetup
from updown.common.domain.trend import TrendState
from updown.marketdata.ingest.timeframes import interval

DEFAULT_LOOKBACK_BARS = 400
"""탐지 창 크기 — `context.builder.DEFAULT_LOOKBACK_BARS` 와 같은 값이다.

⛔ 안정성 파라미터다 (spec §5.6.5). 운영 경로와 **같은 창**으로 재야 측정이 운영을
대리하므로, 여기만 다른 값을 쓰면 리포트가 실제와 어긋난다.
"""

type SetupIdentity = tuple[str, tuple[Decimal, ...], Decimal]
"""셋업 정체성 — `(setup_type, 진입가들, 손절가)`."""

type ProgressCallback = Callable[[int, int], None]
"""진행 상황 수신자 — `(스캔한 봉 수, 전체 봉 수)`.

전체 봉 수를 **함께 넘기는 것**이 핵심이다. 스캔한 수만 주면 받는 쪽이 퍼센트도 ETA 도
계산할 수 없고, 그러면 봉 번호만 찍던 이전과 같아진다.
"""


def _print_progress(scanned: int, total: int) -> None:
    """기본 진행 출력 — 콜백을 주지 않은 호출부의 동작을 보존한다.

    Args:
        scanned: 스캔한 봉 수.
        total: 전체 봉 수.

    Note:
        `flush=True` 가 필수다. 없으면 파일 리다이렉트 시 블록 버퍼링에 갇힌다.
    """
    share = f" ({scanned / total * 100:.1f}%)" if total else ""
    print(f"    {scanned:>7}봉 스캔{share}", flush=True)


def setup_identity(setup: TradeSetup) -> SetupIdentity:
    """같은 구조물에서 나온 셋업을 하나로 묶는 키.

    Args:
        setup: 탐지된 셋업.

    Returns:
        `(셋업 종류, 진입가들, 손절가)`. 같은 구조물이 여러 봉에서 다시 잡혀도 하나로 센다.

    Note:
        가격 조합이 우연히 겹칠 확률은 Decimal 정밀도에서 사실상 0 이다. 대신 룰 버전은
        키에 넣지 않는다 — 한 번의 스캔은 한 버전으로 돌기 때문이다.
    """
    return (
        setup.setup_type,
        tuple(leg.price for leg in setup.entry_plan),
        setup.stop_loss,
    )


@dataclass(slots=True)
class _Sighting:
    """스캔 중 추적하는 셋업 하나.

    Attributes:
        setup: 최초 탐지본.
        first_seen: 처음 나타난 봉 번호.
        last_seen: 마지막으로 나타난 봉 번호.
        trend: **최초 탐지 시점**의 상위 TF 추세. 추세 색인을 주지 않았으면 None.
        ledger: **최초 탐지 시점**의 근거 원장. 플래그를 주지 않았으면 None.
            §4.14 한계 기여의 원자료다 (`evaluation/evidence.py`).

    Note:
        `trend` 를 최초 탐지본 기준으로 잡는 이유는 `setup` 과 같다 — 측정 질문이
        "**그 탐지가** 예측대로 움직였나"이므로 판정 재료가 한 시점에서 와야 한다
        (모듈 docstring "어느 시점의 셋업으로 판정하는가").
    """

    setup: TradeSetup
    first_seen: int
    last_seen: int
    trend: TrendDirection | None = None
    ledger: EvidenceLedger | None = None


@dataclass(frozen=True, slots=True)
class Collected:
    """탐지 결과만 모은 것 — **판정 기한이 아직 안 걸린 상태**.

    Attributes:
        sightings: 고유 셋업들 (최초 탐지본 + 관측 구간).
        occurrences: 중복 제거 **전** 탐지 횟수.
        scanned_bars: 실제로 탐지를 돌린 봉 수.
        blocked_by_gate: 추세 게이트가 기각한 탐지 횟수 — **사유별**이다.

    Note:
        판정(`evaluate`)과 분리한 이유는 비용이다. 기한 민감도(50/100/200봉)를 보려면
        판정을 세 번 해야 하는데, 탐지까지 세 번 돌리면 5m 1년치가 6분에서 18분이 된다.
        **탐지 결과는 기한과 무관**하므로 한 번만 걷는다.

        `blocked_by_gate` 를 사유별로 남기는 이유: "역추세라 걸렸다"와 "추세를 몰라서
        걸렸다"는 원인이 달라 대응이 다르다. 후자가 많으면 상위 TF 백필 문제이고,
        합쳐 두면 그 사실이 게이트 효과로 오인된다 (절대 규칙 #8).
    """

    sightings: tuple[_Sighting, ...]
    occurrences: int
    scanned_bars: int
    blocked_by_gate: Mapping[TrendGateOutcome, int] = field(
        default_factory=lambda: dict[TrendGateOutcome, int]()
    )


@dataclass(frozen=True, slots=True)
class ScanResult:
    """스캔 결과.

    Attributes:
        records: 진입이 발생한 셋업들의 판정 기록.
        detected: 탐지된 **고유** 셋업 수 (중복 제거 후).
        sightings: 중복 제거 **전** 탐지 횟수. `detected` 와의 비를 보면 한 셋업이
            평균 몇 봉 동안 살아 있었는지 알 수 있다.
        scanned_bars: 실제로 탐지를 돌린 봉 수.
        max_hold_bars: 판정에 쓴 기한.
    """

    records: tuple[FollowThroughRecord, ...]
    detected: int
    sightings: int
    scanned_bars: int
    max_hold_bars: int
    stats: FollowThroughStats = field(init=False)

    def __post_init__(self) -> None:
        """집계를 함께 굳힌다 — 호출부가 매번 다시 세지 않게."""
        object.__setattr__(
            self,
            "stats",
            summarize(self.records, no_entry=self.detected - len(self.records)),
        )


def build_context(
    window: Sequence[Candle],
    instrument: Instrument,
    timeframe: Timeframe,
    structure_params: StructureParams,
    trend_lookup: TrendLookup | None = None,
    volume_baseline: VolumeBaseline | None = None,
) -> MarketContext:
    """한 봉 시점의 `MarketContext` 를 만든다.

    Args:
        window: 탐지 창. **전체 캔들은 이 함수에 들어오지 않는다** — 탐지기가 미래를
            볼 수 있는 유일한 경로를 애초에 막는다.
        instrument: 종목.
        timeframe: 시간축.
        structure_params: 다이버전스용 스윙 파라미터.
        trend_lookup: 상위 타임프레임 추세 색인. None 이면 `trend` 가 빈 채로 간다.
        volume_baseline: 거래량 배수 색인 (T03). None 이면 탐지기가 옛 20봉 평균으로
            떨어진다 — ⚠️ 창(400봉)으로는 "같은 시간대 평균"을 만들 수 없다.

    Returns:
        컨텍스트. `candles` 는 `AsOfSequence` 라 창 밖 접근은 `LookaheadError` 다.

    Note:
        `as_of` 를 **마지막 봉의 마감 시각**으로 잡는다 (`ts + interval`). 봉 시작
        시각으로 잡으면 그 봉이 "진행 중"으로 판정돼 창에서 빠진다 (`guard.visible_upto`).

        `structures`(저장소 이력)는 비워 넘긴다 — 구조물 저장소는 DB 에 있고 백테스트
        경로가 아직 없다(P1-8). 반면 **`geometry` 는 반드시 채운다**: 합류가 `fvg` 에서는
        게이트이고 `trendline_channel` 에서는 지배성 필터라, 비우면 두 플러그인이 조용히
        0건이 된다 (`structures/bundle.py`).
    """
    as_of = window[-1].ts + interval(timeframe)
    view = AsOfSequence.until(list(window), as_of, timeframe)
    visible = list(view)
    indicators: dict[Timeframe, Indicators] = {
        timeframe: snapshot.compute(visible, structure_params.swing).at(-1)
    }
    # 상위 TF 추세는 **미리 계산된 색인에서 조회**한다. 창마다 다시 계산하면 창당 O(n)
    # 상태 머신이 붙어 스캔이 몇 배 느려진다 (`gates.trend_gate.TrendLookup`).
    trend: dict[Timeframe, TrendState] = {}
    if trend_lookup is not None:
        state = trend_lookup.at(as_of)
        if state is not None:
            trend[trend_lookup.timeframe] = state
    return MarketContext(
        volume_baseline=volume_baseline,
        instrument=instrument,
        as_of=as_of,
        candles={timeframe: view},
        indicators=indicators,
        structures=(),
        geometry={timeframe: compute_bundle(visible, timeframe, structure_params)},
        trend=trend,
    )


def collect(
    candles: Sequence[Candle],
    instrument: Instrument,
    timeframe: Timeframe,
    detector: SetupDetector,
    *,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    structure_params: StructureParams | None = None,
    progress_every: int = 0,
    on_progress: ProgressCallback | None = None,
    trend_lookup: TrendLookup | None = None,
    apply_gate: bool = True,
    flags: Sequence[FlagEvaluator] = (),
    seasonal_volume: bool = True,
) -> Collected:
    """과거 구간을 걸어가며 셋업을 모은다 — **판정은 하지 않는다**.

    Args:
        candles: `ts` 오름차순 전체 캔들.
        instrument: 종목.
        timeframe: 시간축.
        detector: 측정할 탐지 플러그인. **실제 운영과 같은 객체**다.
        lookback_bars: 탐지 창 크기.
        structure_params: 구조물 파라미터.
        progress_every: N 봉마다 진행 상황을 알린다. 0 이면 알리지 않는다.
        on_progress: 진행 상황 수신자 `(scanned, total)`. None 이면 표준출력에 쓴다.
        trend_lookup: 상위 타임프레임 추세 색인. 주면 셋업마다 **그 시점의 추세가
            기록**되고(`_Sighting.trend`), `apply_gate` 가 True 면 §5.4-2 게이트가 걸린다.
        apply_gate: 역추세 셋업을 실제로 **버릴지**. False 면 전부 모으되 추세만 기록한다.
        flags: 근거 플래그들. 주면 셋업마다 **그 시점의 원장**이 기록된다. 플래그는
            진입을 만들지 않으므로 과탐지 룰(fvg·채널)도 여기서는 무해하다
            (`evaluation/flags.py`).
        seasonal_volume: 거래량 배수를 **같은 시간대 기준선**으로 잴지 (T03). False 면
            옛 20봉 평균으로 되돌린다 — ⛔ **전후 비교 전용 스위치**이며 비교가 끝나면
            지운다. 남겨 두면 어느 기준선으로 잰 값인지 리포트마다 달라진다.

    Returns:
        중복 제거된 셋업들. 게이트를 걸었으면 기각 사유별 개수가 함께 담긴다.

    Note:
        ## `apply_gate=False` 가 필요한 이유 — 게이트 효과를 오염 없이 재려면

        게이트 전/후를 비교하려고 **스캔을 두 번 돌리면 안 된다.** 수집이 계속 도는 탓에
        두 스캔의 봉 수가 달라지고(실측에서 59봉 차이), 그러면 차이가 게이트 때문인지
        구간 때문인지 구분되지 않는다.

        `apply_gate=False` 로 **한 번만 걷고** 추세 라벨로 잘라 보면 두 조건이 완전히 같은
        탐지 결과 위에서 비교된다. 덤으로 "횡보 구간 셋업이 몇 건인가" 같은 질문도
        재스캔 없이 답해진다.

    Note:
        **결정론적이다** (원칙 P1) — 같은 캔들·같은 룰 버전이면 언제 돌려도 같은 값이
        나온다. 난수도, 현재 시각 참조도 없다. `on_progress` 는 관측용이라 결과에
        영향을 주지 않으며, 그래서 결정론을 깨지 않는다.

        진행 알림을 **콜백으로 여는 이유**는 `print` 가 두 가지를 동시에 실패하기
        때문이다: 파일로 리다이렉트하면 블록 버퍼링에 갇히고(17분간 무출력), 봉 번호만
        찍혀 **몇 %인지·언제 끝나는지** 알 수 없다. 콜백이면 `scripts/runtime/_progress.py` 의
        기록기를 끼워 퍼센트·ETA 를 `logs/progress/` 에 남길 수 있다.

        탐지에 넘기는 것은 언제나 **창 슬라이스**다. 전체 리스트는 여기서 탐지기에
        도달하지 않는다 (모듈 docstring).
    """
    settings = structure_params or StructureParams()
    # 평가 목록을 미리 굳힌다 — 원장이 "거짓"과 "평가 안 함"을 가르려면 매 진입에서
    # 같은 목록이어야 한다 (`evidence.EvidenceLedger`).
    evaluated_flags = frozenset(flag.name for flag in flags)
    seen: dict[SetupIdentity, _Sighting] = {}
    occurrences = 0
    scanned = 0
    blocked: dict[TrendGateOutcome, int] = {}
    total = max(0, len(candles) - lookback_bars)
    report = on_progress or _print_progress
    # 🔴 거래량 기준선은 **전체 이력에서 한 번** 만든다 (T03).
    #    창(400봉 = 15m 기준 4.2일)으로는 "같은 시간대 평균"을 만들 수 없다 — 코인
    #    슬롯은 (요일, 시, 분) 이라 표본 4개에 4주가 걸린다. 창 안에서 계산하면 배수가
    #    전부 None 이 되고, 그것은 예외 없이 조용히 "거래량 근거 0건"으로 나타난다.
    #    봉 i 의 배수는 같은 슬롯의 **과거** 봉만으로 만들어지므로 미래 참조가 아니다
    #    (`TrendLookup` 이 같은 근거로 같은 구조를 쓴다).
    #    ⛔ `seasonal_volume=False` 는 **옛 20봉 평균으로 되돌리는 스위치**다. T03
    #    전후 비교를 같은 판에서 재려고 둔 것이며, 비교가 끝나면 지운다 — 남겨 두면
    #    "어느 기준선으로 잰 값인가"가 리포트마다 달라진다.
    volume_baseline = VolumeBaseline.build(candles, instrument.market) if seasonal_volume else None

    for position in range(lookback_bars - 1, len(candles) - 1):
        window = candles[position - lookback_bars + 1 : position + 1]
        scanned += 1
        if progress_every and scanned % progress_every == 0:
            report(scanned, total)

        context = build_context(
            window, instrument, timeframe, settings, trend_lookup, volume_baseline
        )
        # 게이트는 **탐지 뒤**에 건다. 탐지 자체를 건너뛰면 "역추세라 기각된 셋업"과
        # "애초에 셋업이 없던 봉"이 구분되지 않아, 게이트의 효과를 잴 수 없다.
        state = None if trend_lookup is None else trend_lookup.at(context.as_of)
        outcome = TrendGateOutcome.ALLOWED if trend_lookup is None else judge_trend(state)
        for setup in detector.detect(context):
            occurrences += 1
            if apply_gate and not outcome.is_allowed:
                blocked[outcome] = blocked.get(outcome, 0) + 1
                continue
            key = setup_identity(setup)
            existing = seen.get(key)
            if existing is None:
                # 플래그는 **탐지가 난 봉에서만** 평가한다. 모든 봉에서 돌리면 비용이
                # 트리거와 같아지는데(5m 1년치 105,120봉), 필요한 것은 진입이 생긴
                # 봉의 상태뿐이다 (연 514건 = 0.5%).
                ledger = (
                    EvidenceLedger(
                        evaluated=evaluated_flags,
                        present=evaluate_flags(
                            flags, context, None if state is None else state.state
                        ),
                    )
                    if flags
                    else None
                )
                seen[key] = _Sighting(
                    setup=setup,
                    first_seen=position,
                    last_seen=position,
                    trend=None if state is None else state.state,
                    ledger=ledger,
                )
            else:
                existing.last_seen = position

    return Collected(
        sightings=tuple(seen.values()),
        occurrences=occurrences,
        scanned_bars=scanned,
        blocked_by_gate=dict(blocked),
    )


def evaluate(
    candles: Sequence[Candle],
    collected: Collected,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> ScanResult:
    """모은 셋업들의 이행 여부를 판정한다.

    Args:
        candles: `ts` 오름차순 전체 캔들 — **여기서만 미래를 본다**.
        collected: `collect()` 결과.
        max_hold_bars: 이행 판정 기한.

    Returns:
        판정 결과. 진입이 없었던 셋업은 `stats.no_entry` 로만 집계된다.
    """
    last_bar = len(candles) - 1
    records: list[FollowThroughRecord] = []
    for sighting in collected.sightings:
        # 진입 탐색은 탐지기가 셋업을 마지막으로 내놓은 다음 봉까지다 (모듈 docstring).
        horizon = min(sighting.last_seen + 1, last_bar)
        entry = find_entry(candles[: horizon + 1], sighting.setup, after=sighting.first_seen)
        if entry is None:
            continue
        verdict = judge(candles, sighting.setup, entry, max_hold_bars)
        records.append(
            FollowThroughRecord(
                rule_version=sighting.setup.rule_version,
                detected_index=sighting.first_seen,
                entry_index=entry,
                entry_price=entry_level(sighting.setup),
                avg_entry=sighting.setup.avg_entry,
                stop_loss=sighting.setup.stop_loss,
                first_tp=sighting.setup.tp_ladder[0].price,
                rr_ratio=sighting.setup.rr_ratio,
                verdict=verdict,
            )
        )

    records.sort(key=lambda record: record.detected_index)
    return ScanResult(
        records=tuple(records),
        detected=len(collected.sightings),
        sightings=collected.occurrences,
        scanned_bars=collected.scanned_bars,
        max_hold_bars=max_hold_bars,
    )


def scan(
    candles: Sequence[Candle],
    instrument: Instrument,
    timeframe: Timeframe,
    detector: SetupDetector,
    *,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    structure_params: StructureParams | None = None,
    progress_every: int = 0,
    on_progress: ProgressCallback | None = None,
) -> ScanResult:
    """`collect()` + `evaluate()` — 기한 하나만 볼 때의 지름길.

    Args:
        candles: `ts` 오름차순 전체 캔들.
        instrument: 종목.
        timeframe: 시간축.
        detector: 측정할 탐지 플러그인.
        lookback_bars: 탐지 창 크기.
        max_hold_bars: 이행 판정 기한.
        structure_params: 구조물 파라미터.
        progress_every: 진행 알림 간격.
        on_progress: 진행 상황 수신자. None 이면 표준출력.

    Returns:
        판정 결과.

    Note:
        기한 민감도를 볼 때는 이 함수를 반복 호출하지 말고 `collect()` 를 한 번 부른 뒤
        `evaluate()` 를 반복한다 — 탐지가 전체 비용의 대부분이다 (`Collected` docstring).
    """
    collected = collect(
        candles,
        instrument,
        timeframe,
        detector,
        lookback_bars=lookback_bars,
        structure_params=structure_params,
        progress_every=progress_every,
        on_progress=on_progress,
    )
    return evaluate(candles, collected, max_hold_bars)
