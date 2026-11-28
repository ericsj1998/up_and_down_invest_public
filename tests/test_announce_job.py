"""T1045 — 거래소 공지 사건 장치(플랫폼): 제목 규칙 · 목록 응답 꼴 · 받기 작업.

받기 작업은 처음 받기 · 새 공지만 · 실패를, 짝짓기는 폐지 공지 막음을, 표 상태는 모름을 본다.

값은 장치를 재는 중립 값이다(실계좌 갈래 · 제목 규칙은 비공개 시험이 본다).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from updown.analysis.announcements import (
    AnnouncementConfigError,
    TitleRule,
    earliest_hits,
    entry_bar_start,
    rank_name,
    tokens_of,
)
from updown.common.http.outbound import Outbound
from updown.decision.universe_names import NameChainRule
from updown.marketdata.binance.announcements import (
    AnnouncementFeedError,
    Article,
    BinanceAnnouncementSource,
    parse_articles,
)
from updown.orchestration.rebalancer.announce_job import (
    AnnounceJobConfig,
    EventRow,
    FeedState,
    make_event_mapper,
    run_announcements,
)

RULE = TitleRule(
    kind="k",
    source="SRC",
    catalog=7,
    include=("Will Stop",),
    exclude=("Margin",),
    start="Will Stop",
    end=r"\son\s\d{4}",
    token=r"\b[0-9A-Z][0-9A-Z]{1,14}\b",
    stop=frozenset({"AND", "USDT"}),
    pair_prefix="Venue Futures",
    quote="USDT",
)
NOW = datetime(2026, 11, 28, 3, 0, tzinfo=UTC)


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


class TestTitleRule:
    def test_tokens_between_start_and_end(self) -> None:
        assert tokens_of("Exchange Will Stop AAA, BBB and CCC on 2026-01-01", RULE) == (
            "AAA",
            "BBB",
            "CCC",
        )

    def test_exclude_and_missing_include(self) -> None:
        assert tokens_of("Exchange Will Stop Margin AAA", RULE) == ()
        assert tokens_of("Exchange Lists AAA", RULE) == ()

    def test_pair_prefix_strips_the_quote(self) -> None:
        assert tokens_of("Venue Futures Will Stop AAAUSDT and BBBUSDT Perpetual", RULE) == (
            "AAA",
            "BBB",
        )
        # 접두가 없으면 떼지 않는다 · 결제 통화 낱말 자체는 빼는 낱말
        assert tokens_of("Exchange Will Stop AAAUSDT USDT", RULE) == ("AAAUSDT",)

    def test_earliest_per_token(self) -> None:
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        arts = [
            {"id": "2", "title": "X Will Stop AAA", "rel": _ms(t0 + timedelta(days=2))},
            {"id": "1", "title": "X Will Stop AAA and BBB", "rel": _ms(t0)},
            {"id": "3", "title": "X Will Stop CCC", "rel": None},
        ]
        hits = earliest_hits(arts, RULE)
        assert set(hits) == {"AAA", "BBB"}
        assert hits["AAA"].article_id == "1" and hits["AAA"].announced_at == t0

    def test_rank_name_prefers_plain_then_thousand(self) -> None:
        assert rank_name("AAA", {"AAAUSDT", "1000AAAUSDT"}) == "AAAUSDT"
        assert rank_name("AAA", {"1000AAAUSDT"}) == "1000AAAUSDT"
        assert rank_name("AAA", set()) is None

    def test_entry_bar_after_delay(self) -> None:
        at = datetime(2026, 1, 1, 10, 20, 5, tzinfo=UTC)
        assert entry_bar_start(at, 60, timedelta(hours=1)) == datetime(2026, 1, 1, 11, tzinfo=UTC)
        edge = datetime(2026, 1, 1, 10, 59, 0, tzinfo=UTC)
        assert entry_bar_start(edge, 60, timedelta(hours=1)) == datetime(2026, 1, 1, 11, tzinfo=UTC)
        later = datetime(2026, 1, 1, 10, 59, 1, tzinfo=UTC)
        assert entry_bar_start(later, 60, timedelta(hours=1)) == datetime(
            2026, 1, 1, 12, tzinfo=UTC
        )
        with pytest.raises(ValueError):
            entry_bar_start(datetime(2026, 1, 1), 60, timedelta(hours=1))

    def test_bad_regex_is_a_config_error(self) -> None:
        with pytest.raises(AnnouncementConfigError):
            TitleRule("k", "S", 1, ("a",), (), "(", "x", "y")


class TestFeedEnvelope:
    def _payload(self, articles: list[dict[str, Any]], total: int = 1) -> dict[str, Any]:
        return {"code": "000000", "data": {"catalogs": [{"total": total, "articles": articles}]}}

    def test_parse(self) -> None:
        rows, total = parse_articles(self._payload([{"id": 9, "title": "T", "releaseDate": 5}], 3))
        assert rows == [Article("9", "T", 5)] and total == 3

    @pytest.mark.parametrize(
        "bad",
        [
            [],
            {"code": "x"},
            {"data": {"catalogs": []}},
            {"data": {"catalogs": [{"articles": [{"id": 1}]}]}},
        ],
    )
    def test_shape_errors_are_loud(self, bad: object) -> None:
        with pytest.raises(AnnouncementFeedError):
            parse_articles(bad)

    def test_source_sends_the_list_query(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=self._payload([]))

        out = Outbound("T", timeout=1, base_url="https://x", transport=httpx.MockTransport(handler))
        src = BinanceAnnouncementSource(out)
        rows, _total = asyncio.run(src.articles(49, 2, 20))
        assert rows == [] and src.calls == 1
        q = dict(seen[0].url.params)
        assert q == {"type": "1", "catalogId": "49", "pageNo": "2", "pageSize": "20"}
        assert seen[0].url.path == "/bapi/composite/v1/public/cms/article/list/query"


# ── 받기 작업 ───────────────────────────────────────────────────────────────────────────
class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], EventRow] = {}
        self.feeds: dict[str, FeedState] = {}
        self.marks: list[dict[str, Any]] = []

    async def feed(self, kind: str) -> FeedState | None:
        return self.feeds.get(kind)

    async def tokens(self, kind: str) -> set[str]:
        return {t for k, t in self.rows if k == kind}

    async def latest_ms(self, kind: str) -> int | None:
        ats = [r.announced_at for (k, _t), r in self.rows.items() if k == kind]
        return None if not ats else _ms(max(ats))

    async def add(self, rows: Sequence[EventRow]) -> int:
        n = 0
        for r in rows:
            if (r.kind, r.token) not in self.rows:
                self.rows[(r.kind, r.token)] = r
                n += 1
        return n

    async def mark(
        self,
        kind: str,
        *,
        now: datetime,
        ok: bool,
        backfilled: bool = False,
        error: str | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> None:
        old = self.feeds.get(kind, FeedState(kind))
        self.feeds[kind] = FeedState(
            kind,
            backfilled_at=now if backfilled else old.backfilled_at,
            ok_at=now if ok else old.ok_at,
            error_at=None if ok else now,
            error=None if ok else error,
            stats=dict(stats or {}),
        )
        self.marks.append({"kind": kind, "ok": ok, "backfilled": backfilled, "error": error})


class FakeSource:
    def __init__(
        self, pages: list[list[Article]], total: int | None = None, fail: bool = False
    ) -> None:
        self.pages = pages
        self.total = total
        self.fail = fail
        self.asked: list[tuple[int, int, int]] = []

    async def articles(
        self, catalog: int, page: int, size: int
    ) -> tuple[list[Article], int | None]:
        self.asked.append((catalog, page, size))
        if self.fail:
            raise RuntimeError("막힘")
        return (self.pages[page - 1] if page <= len(self.pages) else []), self.total


def _art(i: int, title: str, at: datetime) -> Article:
    return Article(str(i), title, _ms(at))


CFG = AnnounceJobConfig(backfill_pause_s=0.0, map_within_h=24)


class TestJob:
    def test_backfill_writes_history_then_poll_maps_only_new(self) -> None:
        store = FakeStore()
        old = _art(1, "X Will Stop AAA", NOW - timedelta(days=200))
        src = FakeSource([[old]], total=1)
        calls: list[Sequence[str]] = []

        async def mapper(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            calls.append(tokens)
            return {t: (f"{t}USDT", f"{t}_USDT", "ok") for t in tokens}

        (out,) = asyncio.run(
            run_announcements(store, {"SRC": src}, mapper, [RULE], now=NOW, cfg=CFG)
        )
        assert out.action == "backfilled" and out.new_tokens == 1 and out.mapped == 0
        assert (
            store.rows[("k", "AAA")].reason == "history"
            and store.rows[("k", "AAA")].contract is None
        )
        assert store.feeds["k"].backfilled_at == NOW and calls == []
        # 새 공지(같은 이름의 두 번째 공지는 처음이 아니다 · 새 이름만 짝을 짓는다)
        fresh = _art(2, "X Will Stop AAA and BBB", NOW + timedelta(minutes=1))
        src.pages = [[fresh, old]]
        later = NOW + timedelta(minutes=2)
        (out2,) = asyncio.run(
            run_announcements(store, {"SRC": src}, mapper, [RULE], now=later, cfg=CFG)
        )
        assert out2.action == "polled" and out2.new_tokens == 1 and out2.mapped == 1
        row = store.rows[("k", "BBB")]
        assert row.contract == "BBB_USDT" and row.seen_at == later and row.reason == "ok"
        assert calls == [["BBB"]]
        assert store.rows[("k", "AAA")].reason == "history"  # 먼저 적힌 줄이 남는다

    def test_poll_reads_more_pages_when_the_first_page_is_all_new(self) -> None:
        store = FakeStore()
        store.feeds["k"] = FeedState("k", backfilled_at=NOW, ok_at=NOW)
        store.rows[("k", "OLD")] = EventRow(
            "k", "OLD", NOW - timedelta(days=3), NOW, "0", "t", None, None, "history"
        )
        p1 = [_art(5, "X Will Stop EEE", NOW - timedelta(hours=1))]
        p2 = [_art(4, "X Will Stop DDD", NOW - timedelta(days=4))]
        src = FakeSource([p1, p2])
        asyncio.run(run_announcements(store, {"SRC": src}, None, [RULE], now=NOW, cfg=CFG))
        assert [p for _c, p, _s in src.asked] == [1, 2]
        assert {"EEE", "DDD"} <= {t for _k, t in store.rows}

    def test_failure_is_recorded_and_keeps_known_events(self) -> None:
        store = FakeStore()
        store.feeds["k"] = FeedState("k", backfilled_at=NOW, ok_at=NOW - timedelta(hours=5))
        (out,) = asyncio.run(
            run_announcements(
                store, {"SRC": FakeSource([], fail=True)}, None, [RULE], now=NOW, cfg=CFG
            )
        )
        assert out.action == "failed" and "막힘" in out.error
        assert store.feeds["k"].error_at == NOW and store.feeds["k"].ok_at == NOW - timedelta(
            hours=5
        )
        assert store.marks[-1]["ok"] is False

    def test_mapper_failure_fails_the_poll_without_consuming_the_name(self) -> None:
        store = FakeStore()
        store.feeds["k"] = FeedState("k", backfilled_at=NOW, ok_at=NOW)
        src = FakeSource([[_art(1, "X Will Stop ZZZ", NOW - timedelta(minutes=1))]])

        async def broken(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            raise RuntimeError(f"시세 못 받음 {len(tokens)}")

        (out,) = asyncio.run(
            run_announcements(store, {"SRC": src}, broken, [RULE], now=NOW, cfg=CFG)
        )
        assert out.action == "failed"
        assert ("k", "ZZZ") not in store.rows  # 다음 받기가 다시 짝짓는다

    def test_feed_state_known(self) -> None:
        ok = FeedState("k", backfilled_at=NOW, ok_at=NOW)
        assert ok.known(NOW + timedelta(seconds=900), 900)
        assert not ok.known(NOW + timedelta(seconds=901), 900)
        assert not FeedState("k", ok_at=NOW).known(NOW, 900)  # 처음 받기 전


class _Rank:
    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = prices

    async def last_prices(self) -> dict[str, float]:
        return self.prices


class _Order:
    def __init__(self, table: dict[str, tuple[bool, bool]], prices: dict[str, float]) -> None:
        self.table = table
        self.prices = prices

    async def contracts(self) -> dict[str, tuple[bool, bool]]:
        return self.table

    async def last_prices(self) -> dict[str, float]:
        return self.prices


class TestMapper:
    def test_ok_no_rank_delisting_and_price_mismatch(self) -> None:
        rank = _Rank({"AAAUSDT": 1.0, "1000BBBUSDT": 2.0, "DDDUSDT": 3.0, "EEEUSDT": 4.0})
        order = _Order(
            {
                "AAA_USDT": (True, False),
                "BBB_USDT": (True, False),
                "DDD_USDT": (True, True),
                "EEE_USDT": (True, False),
            },
            {"AAA_USDT": 1.0, "BBB_USDT": 0.002, "DDD_USDT": 3.0, "EEE_USDT": 9.0},
        )
        mapper = make_event_mapper(rank, order, NameChainRule())

        async def go() -> Mapping[str, tuple[str | None, str | None, str]]:
            return await mapper(["AAA", "BBB", "CCC", "DDD", "EEE"])

        got = asyncio.run(go())
        assert got["AAA"] == ("AAAUSDT", "AAA_USDT", "ok")
        assert got["BBB"] == ("1000BBBUSDT", "BBB_USDT", "ok")  # 1000 묶음 · 가격비 확인
        assert got["CCC"] == (None, None, "no_rank")
        assert got["DDD"] == ("DDDUSDT", None, "delisting")  # Gate 폐지 공지 계약은 안 든다
        assert got["EEE"][1] is None and got["EEE"][2].startswith("price_mismatch")
