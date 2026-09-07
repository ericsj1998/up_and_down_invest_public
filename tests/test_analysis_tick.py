"""판 없이 **지금 만들어지는 봉** — 꼬리가 자라는 것을 보여 준다 (2026-08-30).

## 사용자 신고

> *"꼬리가 안보이는게 너무 답답하네. 10초봉에서 라이브로 보이는 느낌이 아니라,
>  그냥 10초마다 받아오는 느낌이야."*

## 원인은 하나였다

`/analysis/frame` 은 **마감된 봉까지만** 준다 (`rows[:-1]`). 마감된 봉은 이미 다 그려진
봉이라 **꼬리가 자랄 일이 없다** — 축 주기마다 그림이 통째로 갈리는 것처럼 보인다.

RUN 세부에는 이 입구가 이미 있었다 (`/walkforward/live/{key}/tick`). 다만 **판이
있어야만** 부를 수 있었고, 차트 주문에는 판이 없다.

## ⛔ 이 값은 화면에만 간다

미마감 봉이 구조물 계산에 들어가면 같은 상황에서 매 틱 다른 답이 난다 (절대 규칙 #5).
그래서 `/analysis/frame` 은 여전히 마감 봉만 쓰고, 이 입구는 **따로** 있다.

## 🔴 그리고 반드시 기억통을 지난다

화면이 1초마다 부르는데 그 요청이 그대로 거래소로 나가면 요율 한도에 걸리고, 그러면
**판정용 조회까지 같이 막힌다** — 이 프로젝트가 IP 밴까지 간 사고가 그 모양이었다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import updown.apps.api.analysis as mod
from updown.apps.api.analysis import FORMING_TTL, tick
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.BINANCE, "BTC_USDT", "비트코인", AssetType.COIN, Currency.USD)


def _install(monkeypatch: pytest.MonkeyPatch, rows: Any) -> None:
    """거래소 대신 가짜 어댑터를 꽂는다.

    Args:
        monkeypatch: 픽스처.
        rows: `get_candles` 를 대신할 코루틴.

    Note:
        ⚠️ 시험이 **진짜 거래소를 부르면 안 된다** — 느리고, 값이 매번 다르고,
        요율을 태운다.
    """

    def pick(*_: object, **__: object) -> _Fake:
        return _Fake(rows)

    monkeypatch.setattr(mod.MarketDataProvider, "adapter_for", pick)


def _bar(close: str) -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.S10,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=Decimal(close),
        volume=Decimal(1),
    )


@pytest.fixture(autouse=True)
def clean_box() -> Any:
    """기억통을 비우고 시작한다 — 시험끼리 값을 넘기면 순서에 답이 좌우된다."""
    mod._FORMING.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    mod._FORMING.clear()  # pyright: ignore[reportPrivateUsage]


class TestItRemembersBriefly:
    """🔴 화면이 1초마다 불러도 거래소로는 그보다 자주 안 나간다."""

    def test_the_ttl_is_shorter_than_the_screen_poll(self) -> None:
        """⚠️ 같거나 길면 두 번에 한 번씩 같은 값이 와서 **멈춘 것처럼 보인다**.

        화면 폴링은 1초다 (`useForming.formingMs`).
        """
        assert 0 < FORMING_TTL < 1.0

    @pytest.mark.asyncio
    async def test_a_second_call_reuses_the_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        async def fake(*_: object, **__: object) -> list[Candle]:
            calls["n"] += 1
            return [_bar("105")]

        _install(monkeypatch, fake)
        first = await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        second = await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        assert calls["n"] == 1, "기억통을 안 지났다 — 화면 폴링이 그대로 거래소로 나간다"
        assert first["bar"] == second["bar"]

    @pytest.mark.asyncio
    async def test_different_symbols_do_not_share(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """⚠️ 열쇠가 성기면 **다른 종목의 가격**이 지금 값으로 그려진다."""
        seen: list[str] = []

        async def fake(instrument: Instrument, *_: object, **__: object) -> list[Candle]:
            seen.append(instrument.symbol)
            return [_bar("105")]

        _install(monkeypatch, fake)
        await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        await tick(symbol="ETH_USDT", timeframe="10s", market=Market.BINANCE)
        assert len(seen) == 2

    @pytest.mark.asyncio
    async def test_different_frames_do_not_share(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[Timeframe] = []

        async def fake(_: Instrument, frame: Timeframe, *__: object, **___: object) -> list[Candle]:
            seen.append(frame)
            return [_bar("105")]

        _install(monkeypatch, fake)
        await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        await tick(symbol="BTC_USDT", timeframe="1m", market=Market.BINANCE)
        assert len(seen) == 2


class TestItNeverLies:
    """⛔ 못 받은 것과 "안 움직였다" 는 완전히 다른 사실이다."""

    @pytest.mark.asyncio
    async def test_no_candles_means_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def empty(*_: object, **__: object) -> list[Candle]:
            return []

        _install(monkeypatch, empty)
        got = await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        assert got["bar"] is None, "0 이나 마지막 마감 봉으로 채우면 안 된다"

    @pytest.mark.asyncio
    async def test_a_failure_is_not_remembered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """🔴 실패를 기억하면 **한 번의 요율 제한이 TTL 만큼 이어진다**.

        고치려던 것이 원인이 된다 — `speccache` 가 지키는 규칙과 같다.
        """
        calls = {"n": 0}

        async def flaky(*_: object, **__: object) -> list[Candle]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("요율 한도")
            return [_bar("105")]

        _install(monkeypatch, flaky)
        first = await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        assert first["bar"] is None
        second = await tick(symbol="BTC_USDT", timeframe="10s", market=Market.BINANCE)
        assert second["bar"] is not None, "실패를 기억해서 다시 안 물었다"

    @pytest.mark.asyncio
    async def test_a_bad_frame_is_a_400(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await tick(symbol="BTC_USDT", timeframe="3분", market=Market.BINANCE)


class TestItStaysOutOfJudgement:
    """⛔ 미마감 봉이 판정에 들어가면 같은 상황에서 매 틱 다른 답이 난다 (규칙 #5)."""

    SOURCE = Path("src/updown/apps/api/analysis.py").read_text(encoding="utf-8")

    def test_the_frame_endpoint_still_drops_the_forming_bar(self) -> None:
        assert "closed: list[Candle] = list(rows[:-1]) if rows else []" in self.SOURCE

    def test_the_tick_does_not_build_structures(self) -> None:
        """구조물 계산이 이 입구에 들어오면 초당 한 번 판정이 도는 셈이 된다."""
        head = self.SOURCE.index("async def tick(")
        body = self.SOURCE[head : self.SOURCE.index("def _instrument(")]
        for banned in ("build_frame", "setup_layers", "propose", "useful"):
            assert banned not in body, f"{banned} 가 틱 경로에 있다"


class _Fake:
    """`get_candles` 만 흉내 내는 어댑터 — 시험이 거래소를 부르지 않게."""

    def __init__(self, rows: Any) -> None:
        self._rows = rows

    async def get_candles(self, *args: object, **kwargs: object) -> list[Candle]:
        return await self._rows(*args, **kwargs)

    async def __aenter__(self) -> _Fake:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None
