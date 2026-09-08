"""추세 상태 값 객체 (spec §4.16).

`TrendState` 는 **종목x타임프레임 추세의 단일 진실 원천(SSoT)** 이다. 다른 모듈이 추세를
다시 계산하면 "셋업 게이트가 본 추세"와 "국면 판정이 본 추세"가 갈라진다 — spec §4.16 이
Trend Service 를 독립 모듈로 분리한 이유가 그것이다.

## 상태와 단계는 다른 축이다 ⚠️

| | 무엇인가 | 값 |
|---|---|---|
| `state` | 추세 방향 | `UP` / `DOWN` / `SIDEWAYS` |
| `stage` | DOWN→UP **전환 진행도** | `NONE` / `CHOCH` / `BOS` / `MA_RECLAIM` |

`stage` 가 리스크 배수를 정한다 (§4.16 관찰 목록 단계적 해제 표). 상태가 `DOWN` 인데
`stage=BOS` 인 상태가 정상적으로 존재하며, 그것이 "진입 허용하되 리스크 절반"이다 —
빠른 신호(구조)와 느린 신호(MA)의 트레이드오프를 **단계별 리스크 크기로** 흡수한다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.reports import TrendDirection


class TrendStage(StrEnum):
    """DOWN→UP 전환 진행 단계 (spec §4.16 3단계 확인).

    Attributes:
        NONE: 전환 신호 없음.
        CHOCH: 1단계 — 직전 LH 상향 돌파. **진입은 아직 차단**이고 분석 우선순위만 올린다.
        BOS: 2단계 — HL 형성 + 새 고점 재돌파. **진입 허용, 리스크 % 절반**.
        MA_RECLAIM: 3단계 — 200일선 재탈환. **완전 해제, 정상 리스크**.

    Note:
        단계가 곧 리스크 배수다 (`risk_multiplier`). 이진 판정(전환/미전환)으로 두면
        전환 초입의 고손익비 구간을 통째로 버리거나 가짜 반등에 전량 노출된다.
    """

    NONE = "none"
    CHOCH = "choch"
    BOS = "bos"
    MA_RECLAIM = "ma_reclaim"

    @property
    def risk_multiplier(self) -> Decimal:
        """이 단계에서 허용되는 리스크 배수 (spec §4.16 표).

        Returns:
            0 이면 진입 차단, 0.5 면 리스크 절반, 1 이면 정상.

        Note:
            `NONE`·`CHOCH` 가 **0** 인 것이 핵심이다 — §4.16 은 CHoCH 에서 "진입은 아직
            차단"이라고 못박는다. 0.1 같은 값을 주면 그 조문을 우회하는 것이 된다.
        """
        return {
            TrendStage.NONE: Decimal(0),
            TrendStage.CHOCH: Decimal(0),
            TrendStage.BOS: Decimal("0.5"),
            TrendStage.MA_RECLAIM: Decimal(1),
        }[self]


class StructurePattern(StrEnum):
    """시장 구조 패턴 (spec §4.16 판정 입력 2번).

    Attributes:
        HIGHER: HH/HL — 고점·저점 동반 상승.
        LOWER: LH/LL — 고점·저점 동반 하락.
        MIXED: 어느 쪽도 아니다. **가장 흔한 상태이며 판단 보류를 뜻한다.**
        UNKNOWN: 스윙이 부족해 판정 자체가 불가능하다.

    Note:
        `MIXED` 와 `UNKNOWN` 을 구분하는 것이 중요하다. 전자는 "봤는데 섞여 있다",
        후자는 "볼 데이터가 없다"이며 대응이 다르다 (절대 규칙 #8).
    """

    HIGHER = "higher"
    LOWER = "lower"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TrendEvidence:
    """추세 판정 근거 스냅샷 (spec §4.16 `TrendState.evidence`).

    Attributes:
        structure: 시장 구조 패턴 (HH/HL vs LH/LL).
        ma_alignment: 이동평균 배열 — `bullish`/`bearish`/`mixed`. 산출 불가면 None.
        above_ma200: 종가가 200 SMA 위인가. 산출 불가면 None.
        ma200_slope_per_bar: 200 SMA 의 봉당 기울기. 산출 불가면 None.
        choch_index: CHoCH 가 확정된 봉 번호. 없으면 None.
        bos_index: BOS 가 확정된 봉 번호. 없으면 None.
        liquidity_swept: CHoCH 직전에 전저점 유동성 스윕이 있었는가 (신뢰도 가산).
        volume_expanding: 반등 구간 거래량이 하락 구간보다 큰가 (3단계 보조 확인).

    Note:
        **근거를 스냅샷으로 남기는 것이 필수다** (§4.16, §4.15 와 같은 규칙). 나중에
        "왜 그때 UP 이었나"에 답할 수 없으면 백테스트 재현과 성과 귀속이 무너진다.
    """

    structure: StructurePattern
    ma_alignment: str | None
    above_ma200: bool | None
    ma200_slope_per_bar: Decimal | None
    choch_index: int | None = None
    bos_index: int | None = None
    liquidity_swept: bool = False
    volume_expanding: bool | None = None


@dataclass(frozen=True, slots=True)
class TrendState:
    """종목x타임프레임의 추세 상태 (spec §4.16 출력).

    Attributes:
        instrument: 대상 종목.
        timeframe: 시간축.
        state: 추세 방향.
        stage: DOWN→UP 전환 진행 단계.
        since: 이 상태로 전이된 **봉 시각** (UTC). 벽시계가 아니다 — 백테스트가 같은
            값을 재현해야 한다 (원칙 P1).
        as_of: 이 판정의 기준 봉 시각 (UTC).
        evidence: 판정 근거.

    Note:
        `since` 가 벽시계가 아니라 봉 시각인 것은 P1-1 의 `structures.created_at` 과 같은
        결정이다 — 파생 값이 입력에서 나오면 재현 가능하다.
    """

    instrument: Instrument
    timeframe: Timeframe
    state: TrendDirection
    stage: TrendStage
    since: datetime
    as_of: datetime
    evidence: TrendEvidence

    @property
    def risk_multiplier(self) -> Decimal:
        """허용 리스크 배수 (spec §4.16 표).

        Returns:
            `UP` 이면 **1** (전환이 이미 완료됐다), 그 외에는 단계별 배수.

        Note:
            §4.16 의 단계 표는 **관찰 목록 종목**(= 전환 진행 중)의 단계적 해제 규칙이다.
            `UP` 이 확정됐다면 3단계를 이미 통과한 것이므로 정상 리스크다.

            단계만으로 판정하면 **`UP` 인데 배수가 0** 인 상태가 생긴다 — 이후 봉에서
            국면 시작점이 옮겨져 `stage` 가 `CHOCH` 로 내려갈 수 있기 때문이다.
            "진입 허용인데 수량 0" 은 모순이고, P1-4 개발 중 실제로 그 상태가 나왔다.
        """
        if self.state is TrendDirection.UP:
            return Decimal(1)
        return self.stage.risk_multiplier

    @property
    def entry_allowed(self) -> bool:
        """진입이 허용되는가.

        Returns:
            리스크 배수가 0보다 크면 True.

        Note:
            `DOWN` + `stage=BOS` 도 True 다 — 그것이 §4.16 의 "진입 허용, 단 리스크 %
            절반"이다. 상태만 보고 차단하면 전환 초입의 고손익비 구간을 버린다.

            반대로 **배수가 0인데 허용**되는 경우는 없다. 두 값이 어긋나면 수량 0인
            주문이 승인되고, 그것은 조용한 실패다 (절대 규칙 #8).
        """
        return self.risk_multiplier > 0


@dataclass(frozen=True, slots=True)
class TrendTransition:
    """추세 전이 이벤트 (spec §4.16 `TrendTransition`).

    Attributes:
        instrument: 대상 종목.
        timeframe: 시간축.
        from_state: 이전 상태.
        to_state: 새 상태.
        at: 전이가 확정된 **봉 시각** (UTC).
        stage: 전이 시점의 전환 단계.
        evidence: 전이 근거.

    Note:
        `DOWN → UP` 이벤트가 §4.6 브레이커 ①의 관찰 목록 조건 기반 해제와 재진입 후보
        큐를 트리거한다 (§4.16 소비처). 그래서 이벤트가 `event_logs` 에 남아야 한다.
    """

    instrument: Instrument
    timeframe: Timeframe
    from_state: TrendDirection
    to_state: TrendDirection
    at: datetime
    stage: TrendStage
    evidence: TrendEvidence = field(
        default_factory=lambda: TrendEvidence(
            structure=StructurePattern.UNKNOWN,
            ma_alignment=None,
            above_ma200=None,
            ma200_slope_per_bar=None,
        )
    )

    @property
    def is_recovery(self) -> bool:
        """DOWN → UP 전환인가 — 재진입 후보 큐의 트리거다 (spec §4.16)."""
        return self.from_state is TrendDirection.DOWN and self.to_state is TrendDirection.UP
