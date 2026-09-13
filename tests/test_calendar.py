"""T276 — 주요 일정 달력: 파서(순수) · 설정 · 어댑터(가짜 전송 · 실패는 이유와 함께) · API 묶음.

픽스처는 2026-09-13 실호출 원문을 행만 줄인 것이다(`tests/fixtures/calendar/`).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from updown.apps.api import calendar as calendar_api
from updown.marketdata.calendar.adapter import CalendarAdapter
from updown.marketdata.calendar.client import CalendarClient
from updown.marketdata.calendar.config import CalendarConfig, Fomc, load_calendar_config
from updown.marketdata.calendar.events import (
    ScheduledEvent,
    Watch,
    fomc_events,
    parse_finnhub_earnings,
    parse_fred_release_dates,
    sort_events,
    within,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "calendar"
CONFIG = ROOT / "config" / "calendar.yml"
TODAY = date(2026, 9, 13)
CPI = Watch(10, "미국 CPI", "물가", "https://www.bls.gov/cpi/")
JOBS = Watch(50, "미국 고용", "", None)
PCE = Watch(54, "미국 PCE 물가", "", None)
MARKET_OF = {"COST": "NASDAQ", "AAPL": "NASDAQ"}


def _fixture(name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((FIXTURES / name).read_text(encoding="utf-8")))


class TestParse:
    def test_fred_release_dates_are_future_and_carry_watch_labels(self) -> None:
        got = parse_fred_release_dates(_fixture("fred_release_dates_10.json"), CPI)
        assert [e.on.isoformat() for e in got] == ["2026-10-14", "2026-11-10", "2026-12-10"]
        first = got[0]
        assert first.kind == "macro" and first.title == "미국 CPI" and first.source == "FRED"
        assert first.key == "fred:10:2026-10-14" and first.url == CPI.url
        assert first.detail == {"note": "물가"} and first.symbol is None

    def test_fred_rows_of_another_release_are_dropped(self) -> None:
        # 물은 발표와 다른 id 가 섞여 오면 버린다 — 제목이 틀린 사건을 만들지 않는다.
        assert parse_fred_release_dates(_fixture("fred_release_dates_50.json"), CPI) == []

    def test_fred_daily_release_is_not_a_schedule(self) -> None:
        """FRED 101(FOMC Press Release)은 날마다 한 줄이 온다 — 그래서 설정이 감시하지 않는다."""
        body = _fixture("fred_release_dates_101.json")
        got = parse_fred_release_dates(body, Watch(101, "FOMC", "", None))
        assert [e.on.isoformat() for e in got] == [
            "2026-09-13",
            "2026-09-14",
            "2026-09-15",
            "2026-09-16",
            "2026-09-17",
        ]
        assert 101 not in {w.release_id for w in load_calendar_config(CONFIG).watch}

    def test_finnhub_keeps_only_universe_symbols_by_default(self) -> None:
        body = _fixture("finnhub_earnings.json")
        got = parse_finnhub_earnings(body, MARKET_OF)
        assert [e.symbol for e in got] == ["COST"]
        cost = got[0]
        assert cost.kind == "earnings" and cost.title == "COST 실적 발표"
        assert cost.market == "NASDAQ" and cost.key == f"earnings:COST:{cost.on.isoformat()}"
        assert cost.url is None  # Finnhub 는 링크를 주지 않는다 — 지어내지 않는다
        assert cost.detail["hour"] in {"amc", "bmo", "dmh", ""}
        assert isinstance(cost.detail["eps_estimate"], float)
        everyone = parse_finnhub_earnings(body, MARKET_OF, only_known=False)
        assert len(everyone) == 7 and everyone == sorted(
            everyone, key=lambda e: (e.on, e.symbol or "")
        )

    def test_fomc_events_dedupe_and_sort(self) -> None:
        got = fomc_events(
            [date(2026, 10, 28), date(2026, 9, 16), date(2026, 9, 16)],
            label="FOMC",
            note="n",
            url="u",
        )
        assert [e.key for e in got] == ["fomc:2026-09-16", "fomc:2026-10-28"]
        assert got[0].source == "연준" and got[0].url == "u"

    def test_sort_puts_macro_before_earnings_on_the_same_day(self) -> None:
        a = ScheduledEvent(
            "earnings", date(2026, 10, 14), "AAPL 실적 발표", "Finnhub", "e", symbol="AAPL"
        )
        b = ScheduledEvent("macro", date(2026, 10, 14), "미국 CPI", "FRED", "m")
        c = ScheduledEvent("macro", date(2026, 10, 2), "미국 고용", "FRED", "j")
        assert [e.key for e in sort_events([a, b, c])] == ["j", "m", "e"]
        assert [e.key for e in within([a, b, c], date(2026, 10, 10), date(2026, 10, 20))] == [
            "e",
            "m",
        ]

    def test_event_json_has_no_direction_fields(self) -> None:
        body = ScheduledEvent("macro", TODAY, "t", "s", "k").as_json()
        assert set(body) == {
            "kind",
            "date",
            "title",
            "source",
            "key",
            "symbol",
            "market",
            "url",
            "detail",
        }
        assert not {"direction", "score", "sentiment", "bias"} & set(body)


class TestConfig:
    def test_real_config_loads(self) -> None:
        cfg = load_calendar_config(CONFIG)
        assert [w.release_id for w in cfg.watch] == [10, 54, 50]
        assert all(w.url for w in cfg.watch)
        assert len(cfg.fomc.dates) == 8 and cfg.fomc.dates == tuple(sorted(cfg.fomc.dates))
        assert cfg.earnings_scope == "universe" and cfg.days_ahead == 30

    def test_bad_config_is_loud(self, tmp_path: Path) -> None:
        bad = tmp_path / "calendar.yml"
        bad.write_text("fred:\n  releases:\n    - id: cpi\n", encoding="utf-8")
        with pytest.raises(ValueError, match="정수"):
            load_calendar_config(bad)
        bad.write_text("days_ahead: 400\n", encoding="utf-8")
        with pytest.raises(ValueError, match="days_ahead"):
            load_calendar_config(bad)
        with pytest.raises(ValueError, match="읽을 수 없다"):
            load_calendar_config(tmp_path / "missing.yml")


def _config(
    *, fomc_dates: tuple[date, ...] = (date(2026, 9, 16), date(2026, 10, 28))
) -> CalendarConfig:
    return CalendarConfig(
        watch=(CPI, JOBS, PCE),
        fomc=Fomc("FOMC 금리 결정", "성명", "https://www.federalreserve.gov/", fomc_dates),
        earnings_scope="universe",
        days_ahead=30,
    )


def _client(
    *,
    fred_key: str | None = "fred-secret",
    finnhub_key: str | None = "finnhub-secret",
    fred_down: frozenset[str] = frozenset(),
    finnhub_down: bool = False,
    seen: list[httpx.Request] | None = None,
) -> CalendarClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        host = request.url.host
        if host == "api.stlouisfed.org":
            assert request.url.params["api_key"] == fred_key
            assert request.url.params["include_release_dates_with_no_data"] == "true"
            rid = request.url.params["release_id"]
            if rid in fred_down:
                return httpx.Response(400, json={"error_message": "Bad Request. fred-secret"})
            return httpx.Response(200, json=_fixture(f"fred_release_dates_{rid}.json"))
        if host == "finnhub.io":
            assert request.url.params["token"] == finnhub_key
            if finnhub_down:
                return httpx.Response(401, json={"error": "Invalid API key"})
            return httpx.Response(200, json=_fixture("finnhub_earnings.json"))
        raise AssertionError(f"unexpected host {host}")

    return CalendarClient(fred_key, finnhub_key, transport=httpx.MockTransport(handler))


class TestAdapter:
    @pytest.mark.asyncio
    async def test_all_sources_merge_in_date_order(self) -> None:
        seen: list[httpx.Request] = []
        made = CalendarAdapter(_client(seen=seen), _config())
        events, failures = await made.upcoming(TODAY, date(2026, 11, 12), MARKET_OF)
        assert failures == []
        keys = [e.key for e in events]
        assert "fomc:2026-09-16" in keys and "fomc:2026-10-28" in keys
        assert "fred:54:2026-09-30" in keys and "fred:50:2026-10-02" in keys
        assert "fred:10:2026-10-14" in keys and "fred:10:2026-11-10" in keys
        assert "fred:10:2026-12-10" not in keys  # 창 밖
        assert any(k.startswith("earnings:COST:") for k in keys)
        assert events == sort_events(events)
        # FRED 는 발표마다 한 번(3) + Finnhub 한 번 — 연준 일정은 호출 없음.
        assert len(seen) == 4

    @pytest.mark.asyncio
    async def test_one_dead_source_leaves_the_rest_alive(self) -> None:
        made = CalendarAdapter(_client(fred_down=frozenset({"50"}), finnhub_down=True), _config())
        events, failures = await made.upcoming(TODAY, date(2026, 11, 12), MARKET_OF)
        keys = [e.key for e in events]
        assert "fred:10:2026-10-14" in keys and "fred:54:2026-09-30" in keys
        assert not any(k.startswith("fred:50:") or k.startswith("earnings:") for k in keys)
        failed = {f["key"]: f for f in failures}
        assert set(failed) == {"fred:50", "finnhub"}
        assert failed["fred:50"]["label"] == "미국 고용" and "400" in failed["fred:50"]["reason"]
        assert "401" in failed["finnhub"]["reason"]
        # 🔴 출처가 오류 본문에 키를 되돌려 줘도 이유에 키 값은 없다.
        assert "fred-secret" not in failed["fred:50"]["reason"]
        assert "***" in failed["fred:50"]["reason"]

    @pytest.mark.asyncio
    async def test_missing_keys_fail_without_a_request_and_name_the_variable(self) -> None:
        seen: list[httpx.Request] = []
        made = CalendarAdapter(_client(fred_key=None, finnhub_key=None, seen=seen), _config())
        events, failures = await made.upcoming(TODAY, date(2026, 11, 12), MARKET_OF)
        assert seen == []
        assert [e.key for e in events] == ["fomc:2026-09-16", "fomc:2026-10-28"]
        reasons = {f["key"]: f["reason"] for f in failures}
        assert set(reasons) == {"fred:10", "fred:50", "fred:54", "finnhub"}
        assert "FRED_API_KEY" in reasons["fred:10"] and "FINNHUB_API_KEY" in reasons["finnhub"]


@pytest.mark.asyncio
async def test_api_snapshot_shape_and_cache() -> None:
    seen: list[httpx.Request] = []
    calendar_api.attach_calendar(None, adapter=CalendarAdapter(_client(seen=seen), _config()))
    try:
        body = await calendar_api.upcoming_snapshot(45, today=TODAY)
        assert body["from"] == "2026-09-13" and body["to"] == "2026-10-28" and body["days"] == 45
        assert body["failures"] == []
        assert {e["key"] for e in body["events"]} >= {"fomc:2026-09-16", "fred:10:2026-10-14"}
        assert [w["key"] for w in body["watch"]] == ["fred:10", "fred:50", "fred:54", "fomc"]
        again = await calendar_api.upcoming_snapshot(45, today=TODAY)
        assert again is body and len(seen) == 4  # 30분 캐시 — 같은 창은 다시 부르지 않는다
        # 기본 창은 설정(30일)에서 온다.
        default = await calendar_api.upcoming_snapshot(today=TODAY)
        assert default["days"] == 30 and default["to"] == "2026-10-13"
    finally:
        calendar_api.attach_calendar(None)
