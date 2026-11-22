"""바이낸스 USDT-M 봉 읽기 — 테이커 매수량을 버리지 않는다 · 조회 전용 · 키 없음 (T973).

`mapping.to_candle` 은 봉을 `Candle` 로 옮기며 열 9(테이커 매수 수량)를 버린다. 체결 방향이 필요한
판정은 이 읽기로 `VenueSeries` 를 받는다. 획득 지점은 `marketdata.provider.venue_flow_source` 하나다
(절대 규칙 #0 · `BinanceClient` 를 다른 곳에서 만들지 않는다).
"""

from __future__ import annotations

import asyncio
from array import array
from typing import TYPE_CHECKING, Any, cast

from updown.common.domain.venue_series import VenueSeries

if TYPE_CHECKING:
    from updown.marketdata.binance.client import BinanceClient

PAGE = 1500
"""한 요청 최대 봉 수(`/fapi/v1/klines` limit 상한 · 가중치 10)."""

FRAME_MS = {"15m": 15 * 60_000, "1h": 60 * 60_000}


class FlowSourceError(RuntimeError):
    """봉을 못 받았다 — 부르는 쪽이 새 진입을 쉰다(규칙 #8 · #8-1)."""


def _series(symbol: str, frame_ms: int, rows: list[list[Any]], now_ms: int) -> VenueSeries:
    """응답 행 → 열. 마감 전 봉(닫는 시각 ≥ 지금)은 뺀다.

    Args:
        symbol: 종목.
        frame_ms: 봉 길이.
        rows: `[시작, 시, 고, 저, 종, 양, 닫는 시각, 대금, 건수, 테이커 매수 양, …]` 행들.
        now_ms: 지금(UTC 밀리초) — 이 시각 전에 닫힌 봉만 남긴다.

    Returns:
        오름차순 열(겹침 없음).
    """
    ts: array[int] = array("q")
    cols: tuple[array[float], ...] = tuple(array("d") for _ in range(6))
    last = -1
    for row in sorted(rows, key=lambda r: int(r[0])):
        start = int(row[0])
        if start <= last or int(row[6]) >= now_ms:
            continue
        last = start
        ts.append(start)
        for col, k in zip(cols, (1, 2, 3, 4, 5, 9), strict=True):
            col.append(float(row[k]))
    return VenueSeries(symbol, frame_ms, ts, *cols)


def _rows_of(got: object, symbol: str, interval: str) -> list[list[Any]]:
    """응답 → 봉 행들(칸 10 개 이상인 배열만).

    Raises:
        FlowSourceError: 응답이 배열이 아니다.
    """
    if not isinstance(got, list):
        raise FlowSourceError(f"{symbol} {interval} 봉 응답이 배열이 아니다")
    rows: list[list[Any]] = []
    for row in cast("list[object]", got):
        if isinstance(row, list) and len(cast("list[Any]", row)) >= 10:
            rows.append(cast("list[Any]", row))
    return rows


class BinanceFlowSource:
    """USDT-M 봉 + 테이커 매수 읽기.

    Attributes:
        calls: 지금까지 보낸 요청 수(호출량 보고용).
    """

    def __init__(self, client: BinanceClient, *, pace_s: float = 0.2) -> None:
        """읽기를 만든다.

        Args:
            client: 라이브 공개 주소 클라이언트(키 없음).
            pace_s: 요청 사이 최소 간격(초).
        """
        self._client = client
        self._pace_s = pace_s
        self.calls = 0

    async def aclose(self) -> None:
        """연결을 닫는다."""
        await self._client.aclose()

    async def _page(self, symbol: str, interval: str, end_ms: int | None) -> list[list[Any]]:
        params = {"symbol": symbol, "interval": interval, "limit": str(PAGE)}
        if end_ms is not None:
            params["endTime"] = str(end_ms)
        if self.calls:
            await asyncio.sleep(self._pace_s)
        self.calls += 1
        return _rows_of(await self._client.get_json("/fapi/v1/klines", params), symbol, interval)

    async def latest(self, symbol: str, interval: str, count: int, now_ms: int) -> VenueSeries:
        """마감한 봉 끝 `count` 개 — 뒤에서부터 쪽을 넘겨 받는다.

        Args:
            symbol: 바이낸스 종목(예 `BTCUSDT`).
            interval: `15m` · `1h`.
            count: 받을 봉 수.
            now_ms: 지금(UTC 밀리초).

        Returns:
            오름차순 열. 거래소가 그만큼 이력을 안 주면 있는 만큼.

        Raises:
            FlowSourceError: 응답이 틀렸거나 받은 봉이 없다.
        """
        frame_ms = FRAME_MS[interval]
        rows: list[list[Any]] = []
        end: int | None = None
        while len(rows) < count + 1:
            page = await self._page(symbol, interval, end)
            if not page:
                break
            rows = page + rows
            first = min(int(r[0]) for r in page)
            if len(page) < PAGE:
                break
            end = first - 1
        out = _series(symbol, frame_ms, rows, now_ms).tail(count)
        if not out.ts:
            raise FlowSourceError(f"{symbol} {interval} 마감 봉이 없다")
        return out

    async def since(self, symbol: str, interval: str, start_ms: int, now_ms: int) -> VenueSeries:
        """시작 시각이 `start_ms` 이상인 마감 봉(덧붙이기용 · 한두 쪽).

        Args:
            symbol: 바이낸스 종목.
            interval: `15m` · `1h`.
            start_ms: 받을 첫 봉 시작 시각 하한.
            now_ms: 지금(UTC 밀리초).

        Returns:
            오름차순 열(비었을 수 있다 — 새로 닫힌 봉이 없으면).
        """
        frame_ms = FRAME_MS[interval]
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": str(PAGE),
            "startTime": str(start_ms),
        }
        self.calls += 1
        got = await self._client.get_json("/fapi/v1/klines", params)
        return _series(symbol, frame_ms, _rows_of(got, symbol, interval), now_ms)
