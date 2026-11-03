"""분기 목록 이름 사슬의 재료 — 주문 거래소(Gate) 공개 계약 표 · 현재가 (T758 §5).

두 번 부른다(계약 표 한 번 · 시세 한 번 · 모든 계약). 키 없이 공개 엔드포인트만 쓴다.
"""

from __future__ import annotations

from updown.marketdata.gate.client import GateClient


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
