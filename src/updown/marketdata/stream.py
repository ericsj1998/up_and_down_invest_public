"""거래소 무관 라이브 스트림 계약 (T63 §2b).

`LiveCandle` 은 "웹소켓 봉 + 마감 플래그"라는 거래소 무관 자료형이다 — gate.ws 에
살던 것을 중립 모듈로 올렸다 (T62 P3b 미결 해소). `CandleStream` 은 러너가 스트림에게
요구하는 전부다. 구체 스트림(gate.ws · binance.ws)이 이 계약을 지키고, 소비처는
어느 거래소의 소켓인지 모른다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from updown.common.domain.candle import Candle


@dataclass(frozen=True, slots=True)
class LiveCandle:
    """웹소켓이 준 봉 하나.

    Attributes:
        candle: 도메인 캔들.
        closed: **마감된 봉인가.** 진행 중이면 False.

    Note:
        🔴 이 플래그가 이 자료형의 존재 이유다. 거래소는 진행 중인 봉도 계속 보내는데,
        그것을 확정으로 쓰면 같은 시각의 봉이 여러 값으로 원장에 들어간다.

        ⚠️ **미마감 봉을 버리지 않고 함께 낸다.** 화면은 진행 중인 봉을 보여줘야 하고
        (그게 라이브다), 원장은 마감된 것만 받아야 한다 — 소비처가 다르므로 여기서
        고르지 않고 표시만 한다.
    """

    candle: Candle
    closed: bool


@runtime_checkable
class CandleStream(Protocol):
    """러너 급전 캔들 스트림 계약.

    구현: `GateCandleStream` · `BinanceCandleStream`. 새 거래소 스트림은 이 세 멤버만
    지키면 러너에 꽂힌다 — `GateCandleStream | BinanceCandleStream` 유니온 주석을
    소멸시키는 선언이다 (T63 §2b).
    """

    reconnects: int
    """재연결 누계 — 러너가 데이터 신뢰도 판정에 읽는다."""

    @property
    def is_testnet(self) -> bool:
        """Testnet 소켓인가 — 화면의 live_data 배지가 읽는다."""
        ...

    def stream(self) -> AsyncGenerator[LiveCandle]:
        """봉을 계속 낸다 — 마감·미마감 모두 (`LiveCandle.closed` 로 구분).

        Yields:
            들어오는 순서대로의 봉. 소비자가 마감된 것만 판정에 쓴다.
        """
        ...
