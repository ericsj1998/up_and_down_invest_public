"""T275 — 토스 프록시: 서버 끝점(허용 목록 · 오류 옮김 · 발급 주체 하나) ·
프록시 client(같은 얼굴) · provider 분기 · 권한.

토스 토큰은 client 당 하나라 서버와 연구 PC 가 동시에 토스를 부르면 서로
무효화한다(2026-09-11 실측 30분 127회). 프록시는 발급 주체를 서버 하나로 모은다 —
그래서 시험이 지키는 것은 **"토큰을 받는 자리가 하나뿐인가"** 다.
"""

from __future__ import annotations

import json
from contextlib import AbstractContextManager, nullcontext
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from updown.apps.api.toss_proxy import parse_params, router
from updown.common.config import ConfigurationError, Settings
from updown.common.domain.instrument import Market
from updown.common.security.caps import Cap, required_cap
from updown.common.security.roles import ADMIN_PREFIXES
from updown.marketdata.adapter import RequestBudgetExceededError
from updown.marketdata.provider import (
    TOSS_PROXY_GROUPS,
    TOSS_PROXY_PATHS,
    MarketDataProvider,
    UnsupportedMarketError,
)
from updown.marketdata.toss.adapter import CHART_GROUP, ResultClient, TossAdapter
from updown.marketdata.toss.client import TossApiError, TossAuthError, UnknownSymbolError
from updown.marketdata.toss.proxy_client import PROXY_PATH, TossProxyClient


class FakeDirect:
    """서버 쪽 직접 client 흉내 — 무엇을 받았는지 적고, 정해 둔 답/예외를 낸다."""

    def __init__(self, answer: object = None, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []
        self.answer = answer
        self.raises = raises
        self.requests = 0

    def budget(self, _cap: int) -> AbstractContextManager[None]:
        return nullcontext()

    async def get_result(
        self, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> object:
        self.calls.append((path, group, params))
        self.requests += 1
        if self.raises is not None:
            raise self.raises
        return self.answer

    async def aclose(self) -> None:
        return None


def _settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "dev",
        "database_url": "postgresql+psycopg://x:y@localhost/z",
        "redis_url": "redis://localhost:6379/0",
    }
    base.update(over)
    return Settings(**cast("dict[str, Any]", base))


def _no_proxy(_self: MarketDataProvider) -> bool:
    return False


def _yes_proxy(_self: MarketDataProvider) -> bool:
    return True


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeDirect]:
    fake = FakeDirect(answer=[{"dt": "2026-09-11", "close": "1.0"}])
    monkeypatch.setenv("UPDOWN_MARKETS", "GATE,NASDAQ")
    monkeypatch.setattr(MarketDataProvider, "_shared_toss", fake)
    monkeypatch.setattr(MarketDataProvider, "toss_via_proxy", _no_proxy)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), fake


class TestParams:
    def test_parses_string_object(self) -> None:
        assert parse_params('{"code":"US20220316001","interval":"1m"}') == {
            "code": "US20220316001",
            "interval": "1m",
        }
        assert parse_params("") == {}

    def test_rejects_non_object_and_non_string_values(self) -> None:
        with pytest.raises(ValueError, match="객체"):
            parse_params("[1]")
        with pytest.raises(ValueError, match="문자열"):
            parse_params('{"n": 1}')
        with pytest.raises(ValueError):
            parse_params("{not json")


class TestEndpoint:
    def test_forwards_allowed_path_and_wraps_result(
        self, app_client: tuple[TestClient, FakeDirect]
    ) -> None:
        client, fake = app_client
        res = client.get(
            PROXY_PATH,
            params={
                "path": "/api/v1/candles",
                "group": CHART_GROUP,
                "params": json.dumps({"code": "X", "interval": "1m"}),
            },
        )
        assert res.status_code == 200, res.text
        assert res.json() == {"result": [{"dt": "2026-09-11", "close": "1.0"}]}
        assert fake.calls == [("/api/v1/candles", CHART_GROUP, {"code": "X", "interval": "1m"})]

    def test_rejects_paths_and_groups_outside_the_list(
        self, app_client: tuple[TestClient, FakeDirect]
    ) -> None:
        client, fake = app_client
        bad_path = client.get(PROXY_PATH, params={"path": "/oauth2/token", "group": CHART_GROUP})
        bad_group = client.get(PROXY_PATH, params={"path": "/api/v1/candles", "group": "ANY"})
        bad_params = client.get(
            PROXY_PATH, params={"path": "/api/v1/candles", "group": CHART_GROUP, "params": "[1]"}
        )
        assert (bad_path.status_code, bad_group.status_code, bad_params.status_code) == (
            400,
            400,
            400,
        )
        assert fake.calls == []  # 거른 요청은 토스 근처에도 안 간다

    def test_lists_are_exactly_what_the_adapter_uses(self) -> None:
        assert {
            "/api/v1/candles",
            "/api/v1/orderbook",
            "/api/v1/prices",
            "/api/v1/stocks",
        } == TOSS_PROXY_PATHS
        assert {"MARKET_DATA_CHART", "MARKET_DATA", "STOCK"} == TOSS_PROXY_GROUPS

    @pytest.mark.parametrize(
        ("raised", "status"),
        [
            (UnknownSymbolError("없다", status_code=404), 404),
            # 424 — 502/503 은 nginx `proxy_next_upstream` 이 삼킨다(2026-09-11 실측)
            (TossAuthError("401", status_code=401), 424),
            (TossApiError("429", status_code=429), 424),
        ],
    )
    def test_maps_toss_errors(
        self,
        app_client: tuple[TestClient, FakeDirect],
        raised: Exception,
        status: int,
    ) -> None:
        client, fake = app_client
        fake.raises = raised
        res = client.get(PROXY_PATH, params={"path": "/api/v1/prices", "group": "MARKET_DATA"})
        assert res.status_code == status, res.text

    def test_503_when_this_server_does_not_call_toss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`UPDOWN_MARKETS=GATE` 인 서버는 토큰을 받지 않는다 — 프록시가 조용히
        발급 주체를 둘로 만들면 안 된다."""
        monkeypatch.setenv("UPDOWN_MARKETS", "GATE")
        monkeypatch.setattr(MarketDataProvider, "_shared_toss", None)
        monkeypatch.setattr(MarketDataProvider, "toss_via_proxy", _no_proxy)
        app = FastAPI()
        app.include_router(router)
        res = TestClient(app).get(
            PROXY_PATH, params={"path": "/api/v1/prices", "group": "MARKET_DATA"}
        )
        assert res.status_code == 503
        assert "UPDOWN_MARKETS" in res.text
        assert MarketDataProvider._shared_toss is None  # pyright: ignore[reportPrivateUsage]

    def test_503_when_this_server_is_itself_a_proxy_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UPDOWN_MARKETS", "NASDAQ")
        monkeypatch.setattr(MarketDataProvider, "_shared_toss", FakeDirect(answer={}))
        monkeypatch.setattr(MarketDataProvider, "toss_via_proxy", _yes_proxy)
        app = FastAPI()
        app.include_router(router)
        res = TestClient(app).get(
            PROXY_PATH, params={"path": "/api/v1/prices", "group": "MARKET_DATA"}
        )
        assert res.status_code == 503


class TestAccess:
    def test_admin_only_and_read_only(self) -> None:
        assert "/admin/toss" in ADMIN_PREFIXES
        assert required_cap("GET", PROXY_PATH, live=True) is Cap.MANAGE_USERS
        assert required_cap("GET", PROXY_PATH, live=False) is Cap.MANAGE_USERS


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class TestProxyClient:
    def test_sends_bearer_mode_cookie_and_json_params(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"result": {"ok": True}})

        client = TossProxyClient(
            "https://example.test/api/", SecretStr("updn_secret"), transport=_transport(handler)
        )
        import asyncio

        got = asyncio.run(
            client.get_result("/api/v1/candles", group=CHART_GROUP, params={"code": "X"})
        )
        assert got == {"ok": True}
        req = seen[0]
        assert req.url.path == "/api" + PROXY_PATH
        assert req.headers["authorization"] == "Bearer updn_secret"
        assert req.headers["cookie"] == "updown_mode=live"
        assert req.url.params["path"] == "/api/v1/candles"
        assert req.url.params["group"] == CHART_GROUP
        assert json.loads(req.url.params["params"]) == {"code": "X"}
        assert client.requests == 1

    @pytest.mark.parametrize(
        ("status", "exc"),
        [
            (404, UnknownSymbolError),
            (401, TossAuthError),
            (403, TossAuthError),
            (503, TossApiError),
            (424, TossApiError),
            (400, TossApiError),
        ],
    )
    def test_maps_statuses_like_the_direct_client(
        self, status: int, exc: type[TossApiError]
    ) -> None:
        import asyncio

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"detail": "이유"})

        client = TossProxyClient(
            "https://example.test/api", SecretStr("updn_x"), transport=_transport(handler)
        )
        with pytest.raises(exc):
            asyncio.run(client.get_result("/api/v1/prices", group="MARKET_DATA"))

    def test_budget_counts_proxy_requests(self) -> None:
        import asyncio

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": []})

        client = TossProxyClient(
            "https://example.test/api", SecretStr("updn_x"), transport=_transport(handler)
        )

        async def two() -> None:
            with client.budget(1):
                await client.get_result("/api/v1/prices", group="MARKET_DATA")
                await client.get_result("/api/v1/prices", group="MARKET_DATA")

        with pytest.raises(RequestBudgetExceededError):
            asyncio.run(two())

    def test_roundtrip_through_the_server_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """로컬 client → 서버 끝점 → 서버 직접 client — 어댑터가 받는 `result` 가 같다."""
        import asyncio

        fake = FakeDirect(answer=[{"dt": "x"}])
        monkeypatch.setenv("UPDOWN_MARKETS", "NASDAQ")
        monkeypatch.setattr(MarketDataProvider, "_shared_toss", fake)
        monkeypatch.setattr(MarketDataProvider, "toss_via_proxy", _no_proxy)
        app = FastAPI()
        app.include_router(router)
        client = TossProxyClient(
            "http://server.test", SecretStr("updn_x"), transport=httpx.ASGITransport(app=app)
        )
        got = asyncio.run(
            client.get_result("/api/v1/candles", group=CHART_GROUP, params={"code": "A"})
        )
        assert got == [{"dt": "x"}]
        assert fake.calls == [("/api/v1/candles", CHART_GROUP, {"code": "A"})]
        adapter = TossAdapter(client)  # 어댑터는 프록시를 모른다 — 같은 프로토콜
        assert adapter.requests == 1


class TestProviderBranch:
    def test_proxy_settings_pick_the_proxy_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UPDOWN_MARKETS", "NASDAQ")
        monkeypatch.setattr(MarketDataProvider, "_shared_toss", None)
        settings = _settings(toss_proxy_url="https://s.test/api", toss_proxy_token="updn_t")
        provider = MarketDataProvider(settings)
        assert provider.toss_via_proxy() is True
        client: ResultClient = provider.toss_client(Market.NASDAQ)
        assert isinstance(client, TossProxyClient)
        assert isinstance(provider.adapter_for(Market.NYSE), TossAdapter)
        assert MarketDataProvider._shared_toss is client  # pyright: ignore[reportPrivateUsage]

    def test_half_configured_proxy_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="TOSS_PROXY_TOKEN"):
            _ = _settings(toss_proxy_url="https://s.test/api").toss_proxy
        with pytest.raises(ConfigurationError, match="TOSS_PROXY_URL"):
            _ = _settings(toss_proxy_token="updn_t").toss_proxy
        assert _settings().toss_proxy is None
        assert _settings(toss_proxy_url="", toss_proxy_token="").toss_proxy is None  # 자리만

    def test_live_server_cannot_be_a_proxy_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UPDOWN_MARKETS", "GATE,NASDAQ")
        monkeypatch.setattr(MarketDataProvider, "_shared_toss", None)
        settings = _settings(
            app_env="live",
            live_orders="0",
            toss_proxy_url="https://s.test/api",
            toss_proxy_token="updn_t",
        )
        with pytest.raises(ConfigurationError, match="발급 주체"):
            MarketDataProvider(settings).toss_client()
        assert MarketDataProvider._shared_toss is None  # pyright: ignore[reportPrivateUsage]

    def test_allowlist_gate_still_applies_with_a_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UPDOWN_MARKETS", "BINANCE")
        monkeypatch.setattr(MarketDataProvider, "_shared_toss", None)
        settings = _settings(toss_proxy_url="https://s.test/api", toss_proxy_token="updn_t")
        with pytest.raises(UnsupportedMarketError):
            MarketDataProvider(settings).toss_client()


class TestSwapWindow:
    """블루그린 교체 창의 nginx 502 는 기다렸다 다시 — 서버가 낸 503/424 는 바로 실패."""

    def test_502_is_retried_until_the_slot_is_back(self) -> None:
        import asyncio

        seen: list[int] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            seen.append(1)
            if len(seen) < 4:
                return httpx.Response(502, text="<html>502 Bad Gateway</html>")
            return httpx.Response(200, json={"result": {"ok": 1}})

        client = TossProxyClient(
            "https://example.test/api",
            SecretStr("updn_x"),
            retry_base_s=0.001,
            transport=_transport(handler),
        )
        got = asyncio.run(client.get_result("/api/v1/candles", group=CHART_GROUP))
        assert got == {"ok": 1} and len(seen) == 4

    def test_503_and_424_are_not_retried(self) -> None:
        import asyncio

        for status in (503, 424):
            seen = self._counting(status)
            client = TossProxyClient(
                "https://example.test/api",
                SecretStr("updn_x"),
                retry_base_s=0.001,
                transport=_transport(seen[1]),
            )
            with pytest.raises(TossApiError):
                asyncio.run(client.get_result("/api/v1/prices", group="MARKET_DATA"))
            assert len(seen[0]) == 1

    @staticmethod
    def _counting(status: int) -> tuple[list[int], Any]:
        hits: list[int] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            hits.append(1)
            return httpx.Response(status, json={"detail": "서버가 낸 것"})

        return hits, handler
