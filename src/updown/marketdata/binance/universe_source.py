"""분기 우주 순위 재료 — 바이낸스 USDT-M 공개 API 에서 무기한 목록 · 일봉 거래대금을 받는다 (T758).

순위 규칙(`decision.universe`)이 바이낸스 선물 거래대금으로 정의돼 있어서, 원천을 Gate 로 바꾸면
다른 규칙이 된다. 키 없이 공개 엔드포인트만 쓴다 — 주문 경로가 아니다.

🔴 **닫힌 일봉만 돌려준다.** 분기 첫날 q 의 일봉(아직 형성 중)은 `close_time < q` 로 버린다 —
진짜 앞보기를 막는 것은 이 한 줄이다.

🔴 **부분 자료를 돌려주지 않는다.** 한 심볼이라도 끝내 못 받으면 예외를 던진다 — 순위 경계에서
한 종목만 빠져도 임의 추출이 통째로 달라지므로, 부르는 쪽이 이전 분기 목록을 유지한다
(규칙 #8 · T758 §2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from updown.common.logging.setup import get_logger
from updown.marketdata.binance.client import BinanceApiError, BinanceClient
from updown.marketdata.ratelimit import peak_used_1m

_logger = get_logger("marketdata.binance.universe_source")

_MINUTE_WEIGHT = 2400
"""바이낸스 IP 분당 가중치 한도(실측 2026-11-03 · `probe_binance_public.sh`)."""


class UniverseSourceError(RuntimeError):
    """순위 재료를 온전히 받지 못했다 — 이전 분기 목록을 유지할 일이다."""


class InvalidSymbolStatusError(UniverseSourceError):
    """거래소가 그 심볼의 봉을 상태 때문에 거절했다(`-1122` · 상장 전 등) — 봉이 없다는 답."""


@dataclass(frozen=True, slots=True)
class PerpSymbol:
    """바이낸스 USDT-M 심볼 하나(상태 무관).

    Attributes:
        symbol: 바이낸스 이름(`1000PEPEUSDT`).
        status: `TRADING` · `SETTLING` 등 — 거래 중 판정은 봉으로 하므로 기록만 한다.
        contract_type: `PERPETUAL` · `TRADIFI_PERPETUAL` 등.
        underlying_type: `COIN` 등.
        quote_asset: 결제 통화.
    """

    symbol: str
    status: str
    contract_type: str
    underlying_type: str
    quote_asset: str
    onboard_ms: int | None = None
    """상장 시각(`onboardDate` · ms) — 없으면 None(상장 일수를 모른다)."""

    @property
    def is_coin_perpetual(self) -> bool:
        """코인 무기한인가 — 순위 모집단 판정."""
        return self.contract_type == "PERPETUAL" and self.underlying_type == "COIN"

    @property
    def listed_day(self) -> date | None:
        """상장 UTC 날 — `onboard_ms` 가 없으면 None."""
        if self.onboard_ms is None:
            return None
        return datetime.fromtimestamp(self.onboard_ms / 1000, tz=UTC).date()


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def _ms_at(moment: datetime) -> int:
    return int(moment.astimezone(UTC).timestamp() * 1000)


def parse_daily_quotes(rows: list[Any], q: date) -> dict[date, float]:
    """Klines 1d 응답 → `{UTC 날: 결제 통화 거래대금}` — q 전에 닫힌 봉만.

    Args:
        rows: `/fapi/v1/klines` 응답 배열(열 0 = 시작 ms · 6 = 마감 ms · 7 = quote volume).
        q: 분기 첫날.

    Returns:
        닫힌 일봉의 거래대금.

    Raises:
        UniverseSourceError: 행 모양이 틀렸을 때(조용히 건너뛰지 않는다).
    """
    cut = _ms(q)
    out: dict[date, float] = {}
    for raw in rows:
        try:
            row = cast("list[Any]", raw)
            open_ms, close_ms, quote = int(row[0]), int(row[6]), float(row[7])
        except (TypeError, ValueError, IndexError) as exc:
            raise UniverseSourceError(f"klines 행 모양이 틀렸다: {raw!r:.80}") from exc
        if close_ms >= cut:
            continue
        out[datetime.fromtimestamp(open_ms / 1000, tz=UTC).date()] = quote
    return out


class BinanceUniverseSource:
    """순위 재료 읽기 — 심볼 목록 한 번 + 심볼마다 일봉 한 번.

    Note:
        호출량은 코인 무기한 ≈ 660 x 가중치 2 ≈ 1,320 / 분기. `pace_s` 간격(기본 0.25 초 =
        초당 4 회)으로 늦추고, 헤더로 잰 분당 사용량이 한도의 `pause_ratio` 를 넘으면 쉰다.
        리더 루프 밖(엔진)에서 돈다.
    """

    def __init__(
        self,
        client: BinanceClient,
        *,
        pace_s: float = 0.25,
        pause_ratio: float = 0.6,
        max_tries: int = 4,
    ) -> None:
        """클라이언트와 속도를 받는다.

        Args:
            client: 바이낸스 공개 클라이언트(실거래 주소 · 키 없음).
            pace_s: 호출 사이 최소 간격(초).
            pause_ratio: 분당 가중치가 한도의 이 비율을 넘으면 쉰다.
            max_tries: 심볼 하나의 재시도 횟수(클라이언트 자체 재시도 위에).
        """
        self._client = client
        self._pace_s = pace_s
        self._pause_ratio = pause_ratio
        self._max_tries = max_tries
        self.weight_max = 0
        self.calls = 0
        self.invalid_status: list[str] = []
        """봉 조회를 상태 때문에 거절당한 심볼 — 스냅샷 출처에 남긴다."""

    async def aclose(self) -> None:
        """클라이언트 연결을 닫는다."""
        await self._client.aclose()

    async def _get(
        self, path: str, params: dict[str, str] | None = None
    ) -> list[Any] | dict[str, Any]:
        for attempt in range(1, self._max_tries + 1):
            used = peak_used_1m("BINANCE")
            self.weight_max = max(self.weight_max, used)
            if used > self._pause_ratio * _MINUTE_WEIGHT:
                await asyncio.sleep(15.0)
            await asyncio.sleep(self._pace_s)
            try:
                self.calls += 1
                return await self._client.get_json(path, params)
            except BinanceApiError as exc:
                # 2026-11-04 G3 실측 — 상장 전 심볼(GAIBUSDT)은 400 `-1122 Invalid symbol status`.
                #    다시 물어도 같은 답이라 재시도 안 함(실패로 세면 분기 스냅샷이 통째로 막힌다).
                if exc.status_code == 400 and "-1122" in str(exc):
                    raise InvalidSymbolStatusError(f"{path} {params} — {exc}") from exc
                if attempt == self._max_tries:
                    raise UniverseSourceError(f"{path} {params} — {exc}") from exc
                await asyncio.sleep(2.0**attempt)
        raise UniverseSourceError(f"{path} 재시도 소진")  # pragma: no cover - 위에서 끝난다

    async def perpetual_symbols(self) -> tuple[PerpSymbol, ...]:
        """USDT-M 전 심볼(상태 무관) — `exchangeInfo`.

        Returns:
            심볼들(이름 순).

        Raises:
            UniverseSourceError: 응답을 못 받았거나 모양이 틀렸을 때.
        """
        body = await self._get("/fapi/v1/exchangeInfo")
        if not isinstance(body, dict) or not isinstance(body.get("symbols"), list):
            raise UniverseSourceError("exchangeInfo 모양이 틀렸다")
        out: list[PerpSymbol] = []
        for raw in cast("list[dict[str, Any]]", body["symbols"]):
            onboard = raw.get("onboardDate")
            try:
                onboard_ms = int(onboard) if onboard not in (None, "") else None
            except (TypeError, ValueError):
                onboard_ms = None
            out.append(
                PerpSymbol(
                    symbol=str(raw.get("symbol", "")),
                    status=str(raw.get("status", "")),
                    contract_type=str(raw.get("contractType", "")),
                    underlying_type=str(raw.get("underlyingType", "")),
                    quote_asset=str(raw.get("quoteAsset", "")),
                    onboard_ms=onboard_ms,
                )
            )
        out.sort(key=lambda s: s.symbol)
        return tuple(out)

    async def listed_symbols(self) -> dict[str, date | None]:
        """코인 무기한 · 결제 USDT · 이름이 `…USDT` 인 심볼(상태 무관) → 상장 날 (T1045 순위 폭).

        Returns:
            `{심볼: 상장 UTC 날 | None}`. 상태(`TRADING` · `SETTLING` …)를 거르지 않는다 — 폐지
            직전 종목도 순위 창 안 거래대금이 있으면 순위에 든다(과거 표와 같은 모집단).

        Raises:
            UniverseSourceError: 목록을 못 받았을 때(부분 목록을 돌려주지 않는다).
        """
        return {
            s.symbol: s.listed_day
            for s in await self.perpetual_symbols()
            if s.is_coin_perpetual
            and s.quote_asset == "USDT"
            and s.symbol.isascii()
            and s.symbol.endswith("USDT")
        }

    async def hourly_rows(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, Decimal, Decimal]]:
        """심볼 하나의 `[start, end)` 에 시작한 1H 봉 `(시작, 종가, 거래량)` — 닫힌 봉만 (T1045).

        Args:
            symbol: 바이낸스 이름.
            start: 첫 봉 시작(포함 · UTC).
            end: 끝(제외 · UTC) — 이 시각에 닫히는 봉까지 든다. 부르는 쪽이 `end` 가 지난 뒤에
                부른다(봉 마감 시각 = 다음 봉 시작).

        Returns:
            봉 열(오름차순). 봉이 없으면 빈 열(상장 전 · 폐지 뒤는 정상).

        Raises:
            UniverseSourceError: 재시도 뒤에도 못 받았거나 행 모양이 틀렸을 때.

        Note:
            한 요청에 최대 1,500 봉 — 그보다 긴 구간은 이어 받는다. 가중치: 봉 500 ~ 1,000 개 = 5.
        """
        hour_ms = 3_600_000
        cursor, stop = _ms_at(start), _ms_at(end)
        out: list[tuple[datetime, Decimal, Decimal]] = []
        while cursor < stop:
            want = min(1500, (stop - cursor) // hour_ms)
            if want <= 0:
                break
            try:
                body = await self._get(
                    "/fapi/v1/klines",
                    {
                        "symbol": symbol,
                        "interval": "1h",
                        "startTime": str(cursor),
                        "endTime": str(stop - 1),
                        "limit": str(want),
                    },
                )
            except InvalidSymbolStatusError:
                self.invalid_status.append(symbol)
                return out
            if not isinstance(body, list):
                raise UniverseSourceError(f"{symbol} klines 모양이 틀렸다")
            if not body:
                break
            last = cursor
            for raw in body:
                try:
                    row = cast("list[Any]", raw)
                    open_ms = int(row[0])
                    close = Decimal(str(row[4]))
                    volume = Decimal(str(row[5]))
                except (TypeError, ValueError, IndexError, ArithmeticError) as exc:
                    raise UniverseSourceError(f"klines 행 모양이 틀렸다: {raw!r:.80}") from exc
                if cursor <= open_ms < stop:
                    out.append((datetime.fromtimestamp(open_ms / 1000, tz=UTC), close, volume))
                last = max(last, open_ms)
            cursor = last + hour_ms
        return out

    async def daily_quotes(self, symbol: str, q: date, lookback_days: int) -> dict[date, float]:
        """심볼 하나의 [q - lookback - 1 일, q) 닫힌 일봉 거래대금.

        Args:
            symbol: 바이낸스 이름.
            q: 분기 첫날.
            lookback_days: 순위 창 길이(일).

        Returns:
            `{UTC 날: 거래대금}` — 봉이 없으면 빈 사전(신규 · 폐지 종목은 정상).

        Raises:
            UniverseSourceError: 재시도 뒤에도 못 받았을 때.
        """
        start = q - timedelta(days=lookback_days + 1)
        try:
            body = await self._get(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": "1d",
                    "startTime": str(_ms(start)),
                    "endTime": str(_ms(q) - 1),
                    "limit": str(min(1500, lookback_days + 5)),
                },
            )
        except InvalidSymbolStatusError:
            self.invalid_status.append(symbol)
            return {}
        if not isinstance(body, list):
            raise UniverseSourceError(f"{symbol} klines 모양이 틀렸다")
        return parse_daily_quotes(body, q)

    async def last_prices(self) -> dict[str, float]:
        """`{심볼: 최근 체결가}` — 이름 사슬 가격비 검사용.

        `/fapi/v1/ticker/price` 한 번 · 가중치 2.

        Raises:
            UniverseSourceError: 응답을 못 받았거나 모양이 틀렸을 때.
        """
        body = await self._get("/fapi/v1/ticker/price")
        if not isinstance(body, list):
            raise UniverseSourceError("ticker/price 모양이 틀렸다")
        out: dict[str, float] = {}
        for raw in cast("list[dict[str, Any]]", body):
            try:
                out[str(raw["symbol"])] = float(raw["price"])
            except (KeyError, TypeError, ValueError):
                continue
        return out
