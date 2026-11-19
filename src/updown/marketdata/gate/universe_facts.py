"""분기 목록 이름 사슬의 재료 — 주문 거래소(Gate) 공개 계약 표 · 현재가 (T758 §5).

두 번 부른다(계약 표 한 번 · 시세 한 번 · 모든 계약). 키 없이 공개 엔드포인트만 쓴다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from updown.marketdata.gate.client import GateClient

DAY_SECONDS = 86400


class GateUniverseFacts:
    """Gate USDT 무기한 계약 표 · 현재가 읽기."""

    def __init__(self, client: GateClient) -> None:
        """클라이언트를 받는다.

        Args:
            client: Gate 공개 클라이언트(실거래 주소).
        """
        self._client = client

    async def aclose(self) -> None:
        """클라이언트 연결을 닫는다."""
        await self._client.aclose()

    async def contracts(self) -> dict[str, tuple[bool, bool]]:
        """`{계약 이름: (거래 중, 폐지 공지)}` — 모든 USDT 무기한.

        Raises:
            ValueError: 응답 모양이 틀렸을 때.
        """
        body = await self._client.get_json("/futures/usdt/contracts")
        if not isinstance(body, list):
            raise ValueError("Gate 계약 표 모양이 틀렸다")
        out: dict[str, tuple[bool, bool]] = {}
        for raw in body:
            name = str(raw.get("name", ""))
            status = str(raw.get("status", "trading"))
            out[name] = (status == "trading", bool(raw.get("in_delisting", False)))
        return out

    async def last_prices(self) -> dict[str, float]:
        """`{계약 이름: 최근 체결가}`.

        Raises:
            ValueError: 응답 모양이 틀렸을 때.
        """
        body = await self._client.get_json("/futures/usdt/tickers")
        if not isinstance(body, list):
            raise ValueError("Gate 시세 모양이 틀렸다")
        out: dict[str, float] = {}
        for raw in body:
            try:
                out[str(raw["contract"])] = float(raw["last"])
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def last_closes(self) -> dict[str, Decimal]:
        """`{계약 이름: 최근 체결가}` — 모든 계약을 **한 번**에(Decimal · 0 이하는 뺀다).

        UTC 자정 직후에 부르면 그 값이 방금 닫힌 날의 종가다(몇 초 차이).

        Raises:
            ValueError: 응답 모양이 틀렸을 때.
        """
        body = await self._client.get_json("/futures/usdt/tickers")
        if not isinstance(body, list):
            raise ValueError("Gate 시세 모양이 틀렸다")
        out: dict[str, Decimal] = {}
        for raw in body:
            try:
                name = str(raw["contract"])
                price = Decimal(str(raw["last"]))
            except (KeyError, TypeError, InvalidOperation):
                continue
            if price > 0:
                out[name] = price
        return out

    async def daily_closes(self, contract: str, days: int, *, now: datetime) -> dict[date, Decimal]:
        """한 계약의 **닫힌** UTC 일봉 종가 — 최근 `days` 개까지.

        Args:
            contract: 계약 이름(`BTC_USDT`).
            days: 받을 닫힌 일봉 수.
            now: 지금 시각 — 아직 안 닫힌 오늘 봉을 빼는 데 쓴다.

        Returns:
            `{그 봉의 날짜(UTC): 종가}`. 종가가 0 이하 · 읽을 수 없는 봉은 뺀다.

        Raises:
            ValueError: 응답 모양이 틀렸을 때.
        """
        body = await self._client.get_json(
            "/futures/usdt/candlesticks",
            params={"contract": contract, "interval": "1d", "limit": str(days + 1)},
        )
        if not isinstance(body, list):
            raise ValueError("Gate 일봉 모양이 틀렸다")
        out: dict[date, Decimal] = {}
        for raw in body:
            try:
                start = int(raw["t"])
                price = Decimal(str(raw["c"]))
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            if price <= 0 or start + DAY_SECONDS > now.timestamp():
                continue
            out[datetime.fromtimestamp(start, UTC).date()] = price
        return dict(sorted(out.items())[-days:])
