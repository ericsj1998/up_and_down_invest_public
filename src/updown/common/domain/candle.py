"""캔들 값 객체 (spec §9 `candles`, §12.3).

`ts` 는 **UTC aware 고정**이다. naive datetime 을 허용하면 백필 소스마다 기준이 달라지고
(업비트는 KST/UTC 병존 — P0-7-1), 그 오염은 지표·구조물·백테스트 전부로 번진다.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from updown.common.domain.instrument import Instrument, Timeframe


@dataclass(frozen=True, slots=True)
class Candle:
    """단일 봉 (spec §9 `candles`).

    Attributes:
        instrument: 대상 종목.
        timeframe: 봉의 시간축.
        ts: **봉 시작 시각, UTC aware**. 봉마감 시각이 아니라 시작 시각으로 통일한다
            — 업비트·토스가 모두 시작 시각을 키로 주고, 파티션 경계 판단도 이 값 기준이다.
        open: 시가.
        high: 고가.
        low: 저가.
        close: 종가.
        volume: 거래량.

    Raises:
        ValueError: `ts` 가 naive 이거나 UTC 가 아닌 경우. 조용히 통과시키면 저장 시점에
            타임존이 뒤섞여 원인 추적이 불가능해진다 (절대 규칙 #7, #8).

    Note:
        OHLC 논리 검증(`high >= max(open, close)` 등)과 스파이크 검사는 여기가 아니라
        수집 파이프라인의 무결성 검사기가 한다 (P0-8-4). 위반 봉도 **일단 적재**하고
        구간을 `candle_quality_issues` 에 기록하는 것이 spec §9·plan D-14 의 설계다.
    """

    instrument: Instrument
    timeframe: Timeframe
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        """타임존 불변식을 강제한다 (spec §12.3).

        Raises:
            ValueError: `ts` 가 naive 이거나 UTC 가 아닌 경우 (클래스 docstring).

        Note:
            이것은 구현 로직이 아니라 **타입 계약의 일부**다. 파이썬 타입 힌트로는
            "UTC aware datetime"을 표현할 수 없어 런타임 가드로 보완한다.
        """
        if self.ts.tzinfo is None or self.ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError(f"Candle.ts 는 UTC aware 여야 한다 (spec §12.3). 받은 값: {self.ts!r}")
