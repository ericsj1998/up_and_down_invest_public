"""바이낸스 시세는 **주문이 나가는 곳**에서 온다 (2026-08-30).

사용자: *"테스트넷 콘솔은 테스트넷 쓰는거고, 라이브로 넘어갔을 때 그걸로 쓰는게 맞지."*

## 라이브로 박아 뒀던 것이 두 가지를 깼다

    ① 라이브 선물 WS 가 이 네트워크에서 **데이터를 안 준다**
       구독은 성공하는데(`{"result": null, "id": 1}`) kline 이 0건이다.
       실측: 경로구독 · 합친스트림 · aggTrade **전부 0건** → 20초 REST 폴링으로 내려갔다

    ② 주문은 testnet 에서 체결되는데 화면은 **라이브 호가**를 그렸다
       다른 시장을 보면서 이 시장에 주문하는 것이다. 이 프로젝트는 그 어긋남으로 이미
       사고를 냈다 — 라이브 명세로 만든 손절가가 testnet 에서 거절됐고 20배 숏이
       손절 없이 굴렀다

## 실측 (BTCUSDT · 60초)

    라이브 선물 WS    **0건**
    testnet 선물 WS   113건 · 라이브 대비 괴리 중앙 -0.025%
    testnet REST      1m·5m·15m·1h·4h 전부 400봉 · 거래 0인 봉 0%

## ⚠️ Gate 는 그대로 라이브다

같은 규칙이 두 거래소에서 다른 답을 내는 것이 **맞다** — 사실이 다르기 때문이다.
Gate testnet 호가창은 라이브와 다르고, Gate 라이브 소켓은 멀쩡히 돈다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sourcecheck import code
from updown.marketdata.binance import venue

SRC = Path("src/updown")


class TestItFollowsTheOrderVenue:
    def test_paper_reads_testnet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(venue, "_live", lambda: False)
        assert venue.binance_base() == venue.TESTNET_BASE
        assert venue.binance_ws() == venue.TESTNET_WS

    def test_live_reads_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(venue, "_live", lambda: True)
        assert venue.binance_base() == venue.LIVE_BASE
        assert venue.binance_ws() == venue.LIVE_WS

    def test_rest_and_socket_never_split(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """🔴 하나만 바뀌면 과거 봉과 진행 중 봉이 **다른 시장**에서 온다.

        오른쪽 끝에서 0.05% 짜리 이음매가 생기고, 그것은 "가격이 튀었다" 로 보인다.
        """
        for live in (True, False):
            monkeypatch.setattr(venue, "_live", lambda live=live: live)
            testnet_rest = "testnet" in venue.binance_base()
            testnet_ws = "binancefuture" in venue.binance_ws()
            assert testnet_rest == testnet_ws, f"live={live} 에서 둘이 갈렸다"

    def test_unreadable_settings_fall_to_testnet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """⛔ **모르면 testnet 이다.**

        설정을 못 읽었을 때 라이브로 떨어지면, 그 실수가 실계좌 시세로 화면을 그리면서
        testnet 에 주문을 내는 상태를 만든다 — 정확히 이 파일이 고치는 그 어긋남이다.
        """

        def boom() -> object:
            raise RuntimeError("설정 없음")

        monkeypatch.setattr(venue, "load_settings", boom)
        assert venue._live() is False  # pyright: ignore[reportPrivateUsage]
        assert venue.binance_base() == venue.TESTNET_BASE

    def test_the_env_it_checks_is_the_order_env(self) -> None:
        """⚠️ 판단 기준이 `AppEnv.LIVE` 여야 한다 — 주문 게이트가 보는 그 값이다."""
        source = (SRC / "marketdata" / "binance" / "venue.py").read_text(encoding="utf-8")
        assert "AppEnv.LIVE" in source


class TestItIsActuallyWired:
    """⚠️ 함수만 있고 아무도 안 부르면 화면은 그대로 라이브를 본다."""

    def test_the_provider_uses_it(self) -> None:
        source = (SRC / "marketdata" / "provider.py").read_text(encoding="utf-8")
        assert "binance_base()" in source
        assert "binance_ws()" in source

    def test_the_adapter_passes_the_socket_url_through(self) -> None:
        source = (SRC / "marketdata" / "binance" / "adapter.py").read_text(encoding="utf-8")
        assert "ws_url" in source
        assert "url=self._ws_url" in source

    def test_gate_is_left_on_live(self) -> None:
        """🔴 같은 규칙이 두 거래소에서 다른 답을 내는 것이 **맞다**.

        Gate testnet 호가창은 라이브와 다르고, Gate 라이브 소켓은 멀쩡히 돈다.
        여기까지 같이 바꾸면 멀쩡한 것을 고장 내는 것이다.
        """
        source = code(SRC / "marketdata" / "provider.py")
        head = source.index("if market is Market.GATE:")
        body = source[head : source.index("if market is Market.BINANCE:")]
        assert "GateClient()" in body
        # ⚠️ **코드만 본다** — 주석의 *"testnet 호가창은 라이브와 다르므로"* 를 코드로
        #    읽으면 거짓 실패한다. 오늘만 세 번 그랬다 (`tests/sourcecheck.py`).
        assert "testnet" not in body.lower()
