"""토스 **프록시 어댑터** — 봉은 서버가 합성해 둔 것을 한 번에 (2026-09-11).

`TossAdapter` 는 토스가 1분 원봉만 주므로 15m·1h 를 1분봉 수백 페이지에서 합성한다.
프록시 경유로는 페이지당 0.5~1초라 1h 400봉이 30~40초였다(사용자 "30초 너무 길다").
이 어댑터는 그 자리만 바꾼다 — 서버의 `/admin/toss/candles` 가 **서버 DB 먼저
(`StoredCandles`)** 로 합성된 봉을 주므로 요청 한 번이다. 서버가 밤에 유니버스를 미리
채워 두면(`warm_candles`) 후보 종목은 1~2초다.

그 밖(현재가·호가·장 상태·종목 정보)은 `TossAdapter` 그대로 — 같은 client(`TossProxyClient`)를 쓴다.
"""

from __future__ import annotations

from datetime import datetime

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.session import MarketCalendar
from updown.marketdata.toss.adapter import TossAdapter
from updown.marketdata.toss.proxy_client import TossProxyClient


class TossProxyAdapter(TossAdapter):
    """`TossAdapter` 와 같되 봉만 서버 합성본을 받는다."""

    def __init__(self, client: TossProxyClient, *, calendar: MarketCalendar | None = None) -> None:
        """어댑터를 만든다.

        Args:
            client: 프록시 client — 원시 경로(`get_result`)와 봉 경로(`get_candles`) 둘 다 안다.
            calendar: 장 시간 판정 (시험용).
        """
        super().__init__(client, calendar=calendar)
        self._proxy = client

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """기간 내 봉 — 서버 `/admin/toss/candles` 한 번 (서버 DB 먼저 · 빈 곳만 토스).

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 시작 (UTC).
            end: 끝 (UTC).

        Returns:
            `ts` 오름차순. 서버가 정규장 밖 봉까지 그대로 주므로 받는 쪽(`StoredCandles`)이
            저장·거른다.

        Raises:
            TossApiError: 프록시 실패(서버가 토스를 안 부르는 구성 · 토스 오류 · 전송 오류).
            ValueError: `start > end` 또는 naive 시각.
        """
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end 는 UTC aware 여야 한다")
        if start > end:
            raise ValueError(f"start 가 end 보다 늦다: {start.isoformat()} > {end.isoformat()}")
        return await self._proxy.get_candles(instrument, timeframe, start, end)
