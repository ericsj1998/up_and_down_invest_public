"""분석 리포트 계약 — `TechnicalReport` (spec §4.3).

`FundamentalReport`(§4.4)는 **P3-1 로 이월**한다 (plan D-11). 실제 소비 시점이 Phase 3
이고 그때까지 DART 응답 구조와 적정가 모델(D3-2 미정)이 바뀔 수 있어서다.
원칙: **가까운 소비자가 있는 계약만 지금 고정한다.**

지표 필드가 전부 Optional 인 이유: 상장 3개월짜리 코인에는 200일선이 **존재하지 않는다.**
없는 값을 0 이나 직전값으로 채우면 추세 판정(§4.16)이 조용히 틀린 답을 낸다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.setup import TradeSetup
from updown.common.domain.structure import PriceRange


class TrendDirection(StrEnum):
    """추세 방향 (spec §4.3 `trend`, §4.16 `TrendState.state`).

    Note:
        spec §4.16 은 이 상태를 매번 재계산하는 값이 아니라 **복수의 확정 조건을
        충족해야만 전이하는 상태 머신**으로 규정한다. 경계선 깜빡임을 막는
        히스테리시스가 Trend Service 의 책임이다.
    """

    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class OrderBlockKind(StrEnum):
    """오더블록 방향 (spec §4.3 `key_levels.order_blocks[].type`).

    Attributes:
        DEMAND: 수요 블록 — 지지·진입 근거.
        SUPPLY: 공급 블록 — **진입 근거가 아니라 청산·회피·익절 근거로만** 쓴다
            (spec §12.8 롱 온리).
    """

    DEMAND = "demand"
    SUPPLY = "supply"


@dataclass(frozen=True, slots=True)
class MacdValue:
    """MACD 구성값 (spec §4.3 `indicators.macd`).

    Attributes:
        line: MACD 선.
        signal: 시그널 선.
        histogram: 히스토그램 (line - signal).
    """

    line: Decimal
    signal: Decimal
    histogram: Decimal


@dataclass(frozen=True, slots=True)
class BollingerBands:
    """볼린저 밴드 (spec §4.3 `indicators.bb`).

    Attributes:
        upper: 상단 밴드.
        middle: 중심선.
        lower: 하단 밴드.
    """

    upper: Decimal
    middle: Decimal
    lower: Decimal


@dataclass(frozen=True, slots=True)
class Indicators:
    """공용 지표 묶음 (spec §4.3 `indicators`, §6.1).

    Attributes:
        ma20: 20 **단순**이동평균 (SMA).
        ma60: 60 SMA.
        ma120: 120 SMA.
        ma200: 200 SMA. 장투 전환 판단·국면 판정의 핵심 필터다 (spec §6.1, §4.15).
        ema20: 20 **지수**이동평균.
        ema60: 60 EMA.
        ema120: 120 EMA.
        ema200: 200 EMA.
        rsi14: RSI(14). 0~100 의 무차원 값이라 `float` 을 쓴다.
        macd: MACD.
        bb: 볼린저 밴드.
        atr14: ATR(14). 손절폭 산정의 표준이며 `stop = entry - k*ATR` 로 가격과 직접
            연산되므로 `Decimal` 이다 (spec §6.1).
        vwap: 거래량 가중 평균가.
        volume_ratio: 20일 평균 거래량 대비 배수 (spec §6.1).

    Note:
        지표는 **자체 구현**한다. 라이브러리 위임 금지이며 pandas-ta 는 테스트 대조용
        으로만 쓴다 (spec §2.1, 절대 규칙 #9).

        데이터 부족으로 산출 불가한 지표는 None 이다. 호출부는 None 을 "아직 판단할 수
        없음"으로 다뤄야 하며, 0 으로 취급하면 안 된다.

        **`ma*` 는 SMA, `ema*` 는 EMA 다.** spec §6.1 이 "SMA + EMA 병행"을 요구하므로
        둘을 함께 싣는다. `ma*` 라는 이름은 spec §4.3 의 JSON 키를 그대로 쓴 것이며,
        어느 쪽인지 이름으로 알 수 있어야 해서 EMA 는 접두어를 달았다.

        `macd`/`bb`/`vwap` 은 spec §6.2 **권장(Phase 2)** 이라 Phase 1 에서는 항상
        None 이다 — 필드가 있는 것은 계약을 미리 고정해 둔 것이고, 값이 없는 것이
        미구현의 정직한 표현이다.
    """

    ma20: Decimal | None
    ma60: Decimal | None
    ma120: Decimal | None
    ma200: Decimal | None
    ema20: Decimal | None
    ema60: Decimal | None
    ema120: Decimal | None
    ema200: Decimal | None
    rsi14: float | None
    macd: MacdValue | None
    bb: BollingerBands | None
    atr14: Decimal | None
    vwap: Decimal | None
    volume_ratio: float | None


@dataclass(frozen=True, slots=True)
class OrderBlockLevel:
    """키 레벨로 노출되는 오더블록 (spec §4.3 `key_levels.order_blocks[]`).

    Attributes:
        price_range: 블록의 가격 구간. spec 의 `"range": [a, b]` 에 대응한다.
        kind: 수요/공급.
        volume_score: 거래량 기반 신뢰도 0.0~1.0 (spec §6.3).
        structure_id: 대응하는 `structures` 레코드 id. 차트 하이라이트(§4.13
            `evidence_refs`)와 생애주기 추적의 연결 고리다. 미영속 상태면 None.
    """

    price_range: PriceRange
    kind: OrderBlockKind
    volume_score: float
    structure_id: str | None


@dataclass(frozen=True, slots=True)
class KeyLevels:
    """주요 가격 레벨 (spec §4.3 `key_levels`).

    Attributes:
        prev_low: 전저점. ATR 기반 손절과 병행해 **더 보수적인 쪽**을 택한다 (spec §6.1).
        prev_high: 전고점.
        support: 지지선들.
        resistance: 저항선들.
        order_blocks: 활성 오더블록들.
    """

    prev_low: Decimal | None
    prev_high: Decimal | None
    support: tuple[Decimal, ...]
    resistance: tuple[Decimal, ...]
    order_blocks: tuple[OrderBlockLevel, ...]


@dataclass(frozen=True, slots=True)
class TechnicalReport:
    """기술적 분석 결과 (spec §4.3 출력).

    Attributes:
        instrument: 대상 종목.
        timeframe: 분석 타임프레임 (spec §4.3 입력 `timeframe(들)` 중 이 리포트의 축).
        as_of: 분석 기준 시각 (UTC). spec §4.3 입력의 "분석 시점"이자 §9
            `analysis_reports.created_at` 의 원천이다.
        trend: 추세 방향.
        trend_strength: 추세 강도 0.0~1.0.
        indicators: 공용 지표.
        key_levels: 주요 가격 레벨.
        setups: 탐지된 셋업들 — 셋업 탐지 플러그인들의 출력 (spec §4.3.1).

    Note:
        이 리포트는 **제안만** 담는다. 여기 실린 `stop_loss` 는 확정값이 아니며,
        SSoT 는 RiskManager 다 (spec §5.1, 절대 규칙 #4).
    """

    instrument: Instrument
    timeframe: Timeframe
    as_of: datetime
    trend: TrendDirection
    trend_strength: float
    indicators: Indicators
    key_levels: KeyLevels
    setups: tuple[TradeSetup, ...]
