"""Trend Service — 추세 상태의 SSoT (P1-4-1 · spec §4.16).

## SSoT 규약 — 다른 모듈은 추세를 다시 계산하지 않는다 ⚠️

spec §4.16 이 Trend Service 를 독립 모듈로 분리한 이유가 이것이다: 추세 판단이 기술적
분석·상위 TF 게이트·국면 판정에 흩어지면 **"셋업 게이트가 본 추세"와 "국면 판정이 본
추세"가 갈라진다.** 그 상태에서는 왜 진입이 막혔는지 설명할 수 없다.

| 소비처 | 쓰는 것 | 하지 말아야 하는 것 |
|--------|--------|---------------------|
| 셋업 게이트 (§5.4 게이트 2) | `TrendState.state` | MA 배열을 자체 판정 |
| 서킷 브레이커 ① (§4.6) | `TrendTransition` (DOWN→UP) | 스윙으로 전환 재판정 |
| 재진입 후보 큐 | `TrendTransition.is_recovery` | — |
| Market Regime (§4.15) | `TrendState=UP` 종목 **비율**(breadth) | 종목별 추세 재계산 |
| Position Transition (§4.8) | `TrendState.state` | — |

**계층 관계**: Trend 는 종목 단위, Regime 은 시장 단위다. Regime 이 Trend 를 소비하고
그 역은 아니다 (§4.16 소비처).

## 상태를 DB 에 저장하지 않는다

`TrendState` 는 캔들에서 **결정론적으로 파생**된다. DB 행으로 들고 있으면 두 번째 진실
원천이 생겨 캔들과 어긋날 수 있고(정정 재수집·백필 확장 시), `since` 를 벽시계로 채우는
유혹도 생긴다.

파생으로 두면 같은 캔들에서 항상 같은 상태가 나온다 (원칙 P1). 백테스트와 실시간이 **같은
코드로 같은 답**을 낸다는 뜻이기도 하다.

## 전이 이벤트는 `event_logs` 에 남긴다

§4.6 브레이커 ①의 관찰 목록 해제와 재진입 후보 큐가 이 이벤트를 트리거하므로, "왜 그때
관찰 목록에서 풀렸나"에 답할 수 있어야 한다 (§4.14).

전이 기록은 **`RISK_INCREASING`** 으로 분류한다 — DOWN→UP 전이가 진입을 열기 때문이다.
로그가 실패하면 전이를 확정하지 않는 것이 §1.2.1 의 기본값이며, 그것이 안전한 쪽이다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from updown.analysis.indicators import snapshot
from updown.analysis.indicators.ma import MaAlignment
from updown.analysis.structures.params import SwingParams
from updown.analysis.structures.swing import RollingZigzag, find_pivots
from updown.analysis.trend.choch_bos import TransitionReading, evaluate_transition
from updown.analysis.trend.market_structure import (
    StructureReading,
    ma200_slope,
    read_structure,
)
from updown.analysis.trend.state_machine import (
    decide,
    initial_state,
    structure_broken_down,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.reports import TrendDirection
from updown.common.domain.trend import TrendEvidence, TrendState, TrendTransition
from updown.common.logging.audit import ActionRisk, AuditLogger
from updown.common.logging.setup import get_logger

_logger = get_logger("analysis.trend.service")

MA200_SLOPE_LOOKBACK = 20
"""200 SMA 기울기를 잴 봉 수.

20 은 SMA 단기 기간(spec §6.1)과 같은 값을 쓴 것이다 — 새 숫자를 만들지 않았다.
기울기의 **부호**만 쓰므로 이 값에 성과가 민감하지 않다.
"""

MIN_BARS_FOR_TREND = 210
"""추세 판정에 필요한 최소 봉 수.

200 SMA(§6.1)가 200봉을 요구하고 스윙 확인에 몇 봉이 더 필요하다. 미달이면 판정 자체를
하지 않는다 — 200일선 없이 낸 추세는 §4.16 판정 입력 1번이 빠진 반쪽이다.
"""


class TrendError(RuntimeError):
    """추세를 판정할 수 없다."""


@dataclass(frozen=True, slots=True)
class TrendHistory:
    """봉을 걸어가며 만든 추세 이력.

    Attributes:
        states: 봉별 상태. 판정 불가 구간은 None 이다.
        transitions: 발생한 전이들.

    Note:
        `states` 길이가 캔들 수와 같다 — 인덱스가 봉 번호에 대응한다 (`indicators.series`
        계약 1번과 같은 규칙).
    """

    states: list[TrendState | None]
    transitions: tuple[TrendTransition, ...]

    def at(self, index: int) -> TrendState | None:
        """특정 봉의 상태.

        Args:
            index: 봉 번호. 음수는 뒤에서 센다.

        Returns:
            상태. 판정 불가 구간이면 None.

        Raises:
            TrendError: 범위를 벗어난 인덱스.
        """
        position = index if index >= 0 else len(self.states) + index
        if not 0 <= position < len(self.states):
            raise TrendError(f"index {index} 가 범위(0~{len(self.states) - 1}) 밖이다")
        return self.states[position]

    def flip_count(self) -> int:
        """상태가 바뀐 횟수 — 히스테리시스 검증용 (P1-4 DoD 2).

        Returns:
            전이 수. 히스테리시스가 없으면 경계에서 이 수가 튄다.
        """
        return len(self.transitions)


def evaluate(
    instrument: Instrument,
    timeframe: Timeframe,
    candles: Sequence[Candle],
    swing_params: SwingParams | None = None,
) -> TrendHistory:
    """캔들 전체를 걸어가며 추세 이력을 만든다 (spec §4.16).

    Args:
        instrument: 대상 종목.
        timeframe: 시간축.
        candles: `ts` 오름차순 캔들.
        swing_params: 스윙 파라미터. None 이면 표준값.

    Returns:
        봉별 상태와 전이 목록.

    Raises:
        TrendError: 캔들이 비었거나 시간축이 인자와 다른 경우.

    Note:
        **미래를 보지 않는다.** 봉 `i` 의 판정에는 `candles[:i+1]` 만 쓴다.

        ⚠️ 여기서 갈리는 지점이 하나 있고, 둘을 헷갈리면 미래가 샌다:

        | 하는 것 ⭕ | 하면 안 되는 것 ⛔ |
        |---|---|
        | 피벗을 한 번 찾고 **확인 지연만큼 잘라** 쓴다 | 스윙 열을 통째로 넘기고 인덱스만 자른다 |

        피벗 판정은 국소적이라(`[p-left, p+right]`) 뒤에 봉이 붙어도 안 바뀐다. 그래서
        전체에서 `index + right_bars <= i` 인 앞토막은 **"봉 i 시점에 우측 확인이 끝난
        피벗"과 정확히 같다**. 반면 확인 지연을 빼먹고 자르면 아직 확정되지 않은 스윙이
        섞여 들어간다 — P1-3 에서 실제로 겪은 것이 그쪽이다.

        항등식은 `tests/test_pivot_prefix.py` 가 잠근다. 깨지면 최적화를 되돌린다.

        상태 머신이라 봉마다 직전 상태를 이어받는다 — 그래서 O(n) 순회가 필요하고,
        같은 이유로 결과가 **경로 의존적**이다 (히스테리시스의 본질).
    """
    if not candles:
        raise TrendError("빈 캔들로는 추세를 판정할 수 없다")
    if candles[0].timeframe is not timeframe:
        raise TrendError(f"시간축이 다르다: 인자 {timeframe} vs 캔들 {candles[0].timeframe}")

    settings = swing_params or SwingParams()
    series = snapshot.compute(candles, settings)
    ma200 = series.sma[200]

    states: list[TrendState | None] = [None] * len(candles)
    transitions: list[TrendTransition] = []
    started = False
    current = TrendDirection.SIDEWAYS
    since = candles[0].ts
    # 현재 하락 국면이 시작된 봉 — 이 이전의 CHoCH/BOS 는 무효다 (`choch_bos` docstring).
    phase_start = 0
    # 현재 상태를 유지한 봉 수 추적 — MIN_HOLD_BARS 판정에 쓴다.
    state_since_index = MIN_BARS_FOR_TREND - 1

    # 피벗은 **한 번만** 찾는다. 판정이 국소적(`[p-left, p+right]` 만 본다)이라
    # 뒤에 봉이 붙어도 안 바뀌고, 그래서 `find_pivots(candles[:L])` 은 전체 결과의
    # 앞토막 `index + right < L` 과 정확히 같다 (`tests/test_pivot_prefix.py` 가 잠근다).
    # 봉마다 전체를 다시 훑으면 O(n²) 이고, 800봉 창에서 그것이 백테스트 시간의
    # 97% 였다 (cProfile 실측). **미래를 보지 않는 성질은 그대로다** — 앞토막이
    # 곧 "그 시점에 확인 끝난 피벗"이기 때문이다.
    all_pivots = find_pivots(candles, timeframe, settings)
    # 교대 정리도 좌→우 접기라 같은 이유로 이어붙인다 — 앞토막을 봉마다 다시 접으면
    # 피벗 탐색을 고쳐도 O(n²) 이 그대로 남는다 (실측: 남은 시간의 52%).
    roller = RollingZigzag()
    confirmed = 0

    for index in range(MIN_BARS_FOR_TREND - 1, len(candles)):
        window = candles[: index + 1]
        while (
            confirmed < len(all_pivots)
            and all_pivots[confirmed].index + settings.right_bars <= index
        ):
            confirmed += 1
        swings = roller.upto(all_pivots, confirmed)
        structure = read_structure(swings)
        transition = evaluate_transition(window, swings, ma200[: index + 1], phase_start)
        evidence = _evidence(structure, transition, series, index)

        if not started:
            started = True
            current = initial_state(structure.pattern)
            since = window[-1].ts
            phase_start = index
        else:
            decision = decide(
                current,
                transition,
                structure.pattern,
                structure_broken_down(window, swings),
                ma_bullish=evidence.ma_alignment == MaAlignment.BULLISH.value,
                above_ma200=transition.above_ma200,
                bars_in_state=index - state_since_index,
            )
            if decision.changed:
                transitions.append(
                    TrendTransition(
                        instrument=instrument,
                        timeframe=timeframe,
                        from_state=current,
                        to_state=decision.state,
                        at=window[-1].ts,
                        stage=transition.stage,
                        evidence=evidence,
                    )
                )
                current = decision.state
                since = window[-1].ts
                state_since_index = index
                if current is TrendDirection.DOWN:
                    # 구조가 무너졌으므로 이전 전환 근거를 버리고 새로 쌓는다.
                    phase_start = index

        states[index] = TrendState(
            instrument=instrument,
            timeframe=timeframe,
            state=current,
            stage=transition.stage,
            since=since,
            as_of=window[-1].ts,
            evidence=evidence,
        )

    return TrendHistory(states=states, transitions=tuple(transitions))


def _evidence(
    structure: StructureReading,
    transition: TransitionReading,
    series: snapshot.IndicatorSeries,
    index: int,
) -> TrendEvidence:
    """근거 스냅샷을 만든다 (spec §4.16 `evidence` 필수 저장)."""
    alignment = series.alignment_at(index)
    return TrendEvidence(
        structure=structure.pattern,
        ma_alignment=None if alignment is None else alignment.value,
        above_ma200=transition.above_ma200,
        ma200_slope_per_bar=ma200_slope(series.sma[200][: index + 1], MA200_SLOPE_LOOKBACK),
        choch_index=transition.choch_index,
        bos_index=transition.bos_index,
        liquidity_swept=transition.liquidity_swept,
        volume_expanding=transition.volume_expanding,
    )


class TrendService:
    """추세 상태 조회의 단일 창구 (spec §4.16 SSoT).

    Note:
        **다른 모듈은 이 서비스를 통해서만 추세를 얻는다** (모듈 docstring 표).
        MA 배열이나 스윙으로 자체 판정하는 코드가 생기면 SSoT 가 깨진다.
    """

    def __init__(
        self,
        audit: AuditLogger | None = None,
        swing_params: SwingParams | None = None,
    ) -> None:
        """서비스를 만든다.

        Args:
            audit: 전이 이벤트 기록용 감사 로거. None 이면 기록하지 않는다 —
                순수 함수 테스트와 백테스트에서 DB 없이 돌리기 위한 경로다.
            swing_params: 스윙 파라미터.
        """
        self._audit = audit
        self._params = swing_params or SwingParams()

    def history(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        candles: Sequence[Candle],
    ) -> TrendHistory:
        """추세 이력을 계산한다.

        Args:
            instrument: 대상 종목.
            timeframe: 시간축.
            candles: `ts` 오름차순 캔들.

        Returns:
            봉별 상태와 전이 목록.
        """
        return evaluate(instrument, timeframe, candles, self._params)

    def current(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        candles: Sequence[Candle],
    ) -> TrendState | None:
        """마지막 봉의 추세 상태.

        Args:
            instrument: 대상 종목.
            timeframe: 시간축.
            candles: `ts` 오름차순 캔들.

        Returns:
            상태. 봉이 부족해 판정 불가면 None.
        """
        return self.history(instrument, timeframe, candles).at(-1)

    async def record_transitions(
        self,
        transitions: Sequence[TrendTransition],
    ) -> bool:
        """전이 이벤트를 `event_logs` 에 남긴다 (P1-4 DoD 3 · spec §4.14).

        Args:
            transitions: 기록할 전이들.

        Returns:
            **모든 전이가 안전하게 기록됐는가.** False 면 호출부는 리스크 증가 행동
            (관찰 목록 해제·신규 진입)을 **보류**해야 한다.

        Note:
            `RISK_INCREASING` 으로 분류한다 — DOWN→UP 전이가 진입을 여는 사건이기
            때문이다 (spec §1.2.1). 로그가 실패했는데 진입을 열면 "왜 그때 풀렸나"에
            답할 수 없는 상태로 돈이 나간다.

            감사 로거가 없으면 True 다 — 기록 경로를 아예 안 쓰는 백테스트·테스트를
            막지 않는다. 운영 배선에서는 로거를 반드시 주입한다.
        """
        if self._audit is None:
            return True
        all_recorded = True
        for event in transitions:
            attempt = await self._audit.record(
                event_type="trend.transition",
                module="analysis.trend",
                risk=ActionRisk.RISK_INCREASING,
                payload={
                    "symbol": event.instrument.symbol,
                    "timeframe": event.timeframe.value,
                    "from_state": event.from_state.value,
                    "to_state": event.to_state.value,
                    "stage": event.stage.value,
                    "at": event.at.isoformat(),
                    "is_recovery": event.is_recovery,
                    "structure": event.evidence.structure.value,
                    "liquidity_swept": event.evidence.liquidity_swept,
                    "above_ma200": event.evidence.above_ma200,
                },
            )
            if not attempt.may_proceed:
                all_recorded = False
                _logger.warning(
                    "trend.transition_log_failed",
                    symbol=event.instrument.symbol,
                    to_state=event.to_state.value,
                )
        return all_recorded
