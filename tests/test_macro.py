"""T262 — 거시 지표: 구간(순수) · 출처 파서(순수) · 어댑터(가짜 전송 · 실패는 이유와 함께)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from updown.common.domain.macro import Indicator, cpi_yoy, pct_change, vix_band
from updown.marketdata.macro.adapter import KEYS, MacroAdapter
from updown.marketdata.macro.client import MacroClient
from updown.marketdata.macro.parse import (
    MacroParseError,
    parse_bls_series,
    parse_cboe_vix_csv,
    parse_effr,
    parse_yahoo_chart,
)


class TestDomain:
    def test_vix_bands_follow_the_user_definition(self) -> None:
        assert vix_band(Decimal("12.3")) == ("안정", "calm")
        assert vix_band(Decimal("19.99")) == ("안정", "calm")
        assert vix_band(Decimal(20)) == ("약한 공포", "fear")
        assert vix_band(Decimal("29.9")) == ("약한 공포", "fear")
        assert vix_band(Decimal(30)) == ("강한 공포", "panic")
        assert vix_band(Decimal(80)) == ("강한 공포", "panic")

    def test_pct_change_and_cpi_yoy(self) -> None:
        assert pct_change(Decimal(110), Decimal(100)) == Decimal(10)
        assert pct_change(Decimal(1), None) is None
        assert pct_change(Decimal(1), Decimal(0)) is None
        points = [
            ("2026-07", Decimal("333.918")),
            ("2025-07", Decimal("323.048")),
            ("2026-06", Decimal(333)),
        ]
        period, index, yoy = cpi_yoy(points)
        assert period == "2026-07" and index == Decimal("333.918")
        assert yoy is not None and yoy.quantize(Decimal("0.01")) == Decimal("3.36")
        assert cpi_yoy([("2026-07", Decimal(1))])[2] is None
        with pytest.raises(ValueError):
            cpi_yoy([])

    def test_indicator_json_keeps_precision(self) -> None:
        made = Indicator("vix", "VIX", Decimal("16.46"), "pt", "x", band="안정", tone="calm")
        body = made.as_json()
        assert body["value"] == "16.46" and body["as_of"] is None and body["band"] == "안정"


YAHOO = {
    "chart": {
        "result": [
            {
                "meta": {
                    "symbol": "^VIX",
                    "regularMarketPrice": 16.46,
                    "chartPreviousClose": 14.32,
                    "regularMarketTime": 1788984901,
                },
                # 전일 종가는 봉의 끝에서 둘째(15.72) — chartPreviousClose(14.32)는 5일 전이다.
                "indicators": {"quote": [{"close": [14.32, 14.53, 15.30, 15.72, 16.46]}]},
            }
        ],
        "error": None,
    }
}
EFFR = {
    "refRates": [
        {
            "effectiveDate": "2026-09-08",
            "type": "EFFR",
            "percentRate": 3.63,
            "targetRateFrom": 3.50,
            "targetRateTo": 3.75,
        }
    ]
}
BLS = {
    "status": "REQUEST_SUCCEEDED",
    "Results": {
        "series": [
            {
                "seriesID": "CUUR0000SA0",
                "data": [
                    {"year": "2026", "period": "M08", "value": "-"},
                    {"year": "2026", "period": "M07", "value": "333.918"},
                    {"year": "2026", "period": "M13", "value": "0"},
                    {"year": "2025", "period": "M07", "value": "323.048"},
                ],
            }
        ]
    },
}
CBOE = (
    "DATE,OPEN,HIGH,LOW,CLOSE\n"
    "09/08/2026,15.56,15.94,15.22,15.72\n"
    "09/09/2026,15.65,16.68,15.57,16.46\n"
)


class TestParsers:
    def test_yahoo_effr_bls_cboe(self) -> None:
        price, prev, as_of = parse_yahoo_chart(YAHOO)
        assert price == Decimal("16.46") and prev == Decimal("15.72") and as_of is not None
        rate, low, high, day = parse_effr(EFFR)
        assert rate == Decimal("3.63") and (low, high) == (Decimal("3.5"), Decimal("3.75"))
        assert day == "2026-09-08"
        points = parse_bls_series(BLS)
        assert ("2026-07", Decimal("333.918")) in points
        assert all(p[0][-2:] != "13" for p in points)
        assert all(p[0] != "2026-08" for p in points)  # "-" 달은 뺀다
        close, prev_close, when = parse_cboe_vix_csv(CBOE)
        assert (close, prev_close, when) == (Decimal("16.46"), Decimal("15.72"), "09/09/2026")

    def test_bad_shapes_are_loud(self) -> None:
        with pytest.raises(MacroParseError):
            parse_yahoo_chart({"chart": {"result": None, "error": {"code": "Not Found"}}})
        with pytest.raises(MacroParseError):
            parse_effr({"refRates": []})
        with pytest.raises(MacroParseError):
            parse_bls_series({"status": "REQUEST_NOT_PROCESSED", "message": ["daily limit"]})
        with pytest.raises(MacroParseError):
            parse_cboe_vix_csv("DATE,OPEN\n")


class _Toss:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def exchange_rate(self, base: str = "USD", quote: str = "KRW") -> dict[str, Any]:
        del base, quote
        if self.fail:
            raise RuntimeError("토스 401")
        return {"rate": "1395.2", "midRate": "1390.0", "validFrom": "2026-09-10T09:00:00+09:00"}

    async def indicator_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        del symbols
        if self.fail:
            raise RuntimeError("토스 401")
        return [
            {"symbol": "KOSPI", "lastPrice": "3450.12", "timestamp": "2026-09-10T09:30:00+09:00"},
            {"symbol": "KR_BOND_10Y", "lastPrice": "3.21", "timestamp": None},
        ]


def _client(*, yahoo_down: frozenset[str] = frozenset(), bls_down: bool = False) -> MacroClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "finance.yahoo.com" in url:
            symbol = unquote(url.split("/chart/")[1].split("?")[0])
            if symbol in yahoo_down:
                return httpx.Response(429, text="Too Many Requests")
            body = json.loads(json.dumps(YAHOO))
            body["chart"]["result"][0]["meta"]["symbol"] = symbol
            if symbol == "^TNX":
                body["chart"]["result"][0]["meta"]["regularMarketPrice"] = 4.12
            return httpx.Response(200, json=body)
        if "newyorkfed" in url:
            return httpx.Response(200, json=EFFR)
        if "api.bls.gov" in url:
            body = BLS if not bls_down else {"status": "REQUEST_NOT_PROCESSED"}
            return httpx.Response(200, json=body)
        if "cboe.com" in url:
            return httpx.Response(200, text=CBOE)
        return httpx.Response(404)

    return MacroClient(transport=httpx.MockTransport(handler))


class TestAdapter:
    @pytest.mark.asyncio
    async def test_all_sources_and_order(self) -> None:
        made = MacroAdapter(_client(), _Toss())
        found, failures = await made.indicators()
        keys = [i.key for i in found]
        # KOSDAQ 은 토스 응답에 없어 실패 목록으로 — 조용히 빠지지 않는다.
        assert keys == [k for k in KEYS if k != "kosdaq"]
        assert failures == [{"key": "kosdaq", "label": "코스닥", "reason": "응답에 없음"}]
        by = {i.key: i for i in found}
        assert by["vix"].band == "안정" and by["vix"].tone == "calm"
        assert by["us10y"].value == Decimal("4.12") and by["us10y"].unit == "%"
        assert by["usdkrw"].value == Decimal("1390.0") and "토스" in by["usdkrw"].source
        assert by["effr"].value == Decimal("3.63") and "3.5~3.75" in by["effr"].note
        assert by["cpi"].value.quantize(Decimal("0.01")) == Decimal("3.36")
        assert by["kr10y"].as_of is None and by["kospi"].as_of is not None

    @pytest.mark.asyncio
    async def test_vix_falls_back_to_cboe_and_others_fail_with_reason(self) -> None:
        client = _client(yahoo_down=frozenset({"^VIX", "NQ=F"}), bls_down=True)
        made = MacroAdapter(client, _Toss(fail=True))
        found, failures = await made.indicators(("vix", "nq", "cpi", "usdkrw", "kospi"))
        by = {i.key: i for i in found}
        assert "CBOE" in by["vix"].source and by["vix"].value == Decimal("16.46")
        failed = {f["key"]: f["reason"] for f in failures}
        assert set(failed) == {"nq", "cpi", "usdkrw", "kospi"}
        assert "429" in failed["nq"] and "BLS" in failed["cpi"] and "401" in failed["usdkrw"]

    @pytest.mark.asyncio
    async def test_keys_filter_and_no_toss(self) -> None:
        made = MacroAdapter(_client(), None)
        found, failures = await made.indicators(("effr", "usdkrw"))
        assert [i.key for i in found] == ["usdkrw", "effr"] and failures == []
        assert "야후" in found[0].note
