"""T1057 — 공급 일정 사건 장치(플랫폼): 흘려 읽기 · 코인 표 · 사건 셈 · 받기 · 작업 · 순위 문.

값은 장치를 재는 중립 값이다(실계좌 갈래 · 범주 · 문턱은 비공개 시험이 본다).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
import threading
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest

from updown.analysis.announcements import AnnouncementConfigError
from updown.analysis.supply_schedule import (
    SEEN_SUFFIX,
    SupplyEvent,
    SupplyRule,
    coin_symbol,
    day_events,
    load_supply_rules,
    passes,
    rank_names,
    rank_symbol,
    supply_before,
)
from updown.apps import supply_fetch
from updown.marketdata.coingecko.coins import CoinList, CoinListError, parse_coin_list
from updown.marketdata.defillama import emissions
from updown.marketdata.defillama.emissions import (
    Schedule,
    ScheduleFeedError,
    Section,
    parse_protocol_list,
    parse_schedule,
)
from updown.marketdata.file_fetch import FetchInfo, FileFetchError, fetch_to
from updown.orchestration.rebalancer.announce_job import EventRow, FeedState, feeds_known
from updown.orchestration.rebalancer.supply_job import (
    PENDING,
    SupplyFeedError,
    SupplyJobConfig,
    load_supply_job_config,
    rank_members,
    refresh_due,
    run_supply_events,
    spawn_fetch,
)

H = timedelta(hours=1)
DAY = 86400
NOW = datetime(2026, 11, 28, 1, 23, 40, tzinfo=UTC)
RULE = SupplyRule(
    kind="kind_x",
    source="SRC",
    categories=frozenset({"a", "b"}),
    release_type="step",
    days_before=2,
    min_size_pct=1.5,
    quote="USDT",
    prefixes=("", "10"),
    exclude_prefix=("XX",),
    rank_source="R",
    rank_top=2,
    rank_days=3,
    rank_min_age_days=5,
)
CFG = SupplyJobConfig(
    min_list=2, min_list_ratio=0.8, max_failed_ratio=0.34, pair_within_h=4, stale_h=48
)


def _day_n(d: date) -> int:
    return (d - date(1970, 1, 1)).days


def _ts(d: date) -> int:
    return _day_n(d) * DAY


# ── 일정 파일 흘려 읽기 ───────────────────────────────────────────────────────────────────
def _schedule_doc(n_points: int = 40) -> dict[str, Any]:
    base = _ts(date(2026, 1, 1))
    sec_a = [
        {"timestamp": base + k * DAY, "unlocked": float(k * 10), "rawEmission": 1, "burned": 0}
        for k in range(n_points)
    ]
    # 시각이 뒤섞이고 같은 시각이 겹치는 구역 · null 값
    sec_b = [
        {"timestamp": base + 5 * DAY, "unlocked": 7, "rawEmission": 0, "burned": 0},
        {"timestamp": base + 2 * DAY, "unlocked": None, "rawEmission": 0, "burned": 0},
        {"timestamp": base + 2 * DAY, "unlocked": 3.5, "rawEmission": 0, "burned": 0},
        {"timestamp": base + 9 * DAY, "unlocked": 1e3, "rawEmission": 0, "burned": 0},
    ]
    return {
        "documentedData": {
            "data": [
                {"label": 'Team "core" \\ 이름', "data": sec_a},
                {"label": "Empty", "data": []},
                {"label": "B", "data": sec_b},
            ],
            "tokenAllocation": {"current": {"x": 1}},
        },
        "metadata": {
            "token": "coingecko:aaa",
            "events": [
                {
                    "timestamp": base + 20 * DAY + 3600,
                    "noOfTokens": [30],
                    "category": "a",
                    "unlockType": "step",
                    "description": "긴 설명",
                    "kind": "x",
                },
                {"timestamp": "bad", "noOfTokens": [1], "category": "a", "unlockType": "step"},
            ],
        },
        "name": "Aaa",
        "gecko_id": "aaa",
        "supplyMetrics": {"maxSupply": 1},
    }


def _research_series(doc: Mapping[str, Any]) -> list[list[tuple[int, float]]]:
    """연구 `load_events` 의 구역 셈(json.loads 길) — 대조 기준."""
    out: list[list[tuple[int, float]]] = []
    for sec in cast("list[dict[str, Any]]", doc["documentedData"]["data"]):
        rows = cast("list[dict[str, Any]]", sec.get("data") or [])
        pts = [
            (int(p["timestamp"]), float(p.get("unlocked") or 0)) for p in rows if "timestamp" in p
        ]
        if pts:
            out.append(sorted(pts))
    return out


class TestScan:
    def test_streaming_scan_equals_json_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(emissions, "CHUNK", 512)  # 덩이 사이에 점 · 이름이 걸리게
        monkeypatch.setattr(emissions, "MARGIN", 256)
        doc = _schedule_doc(80)
        path = tmp_path / "a.json"
        path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False), "utf-8")
        got = parse_schedule(path, "aaa")
        want = _research_series(doc)
        assert [list(zip(s.ts, s.released, strict=True)) for s in got.sections] == want
        assert got.sections[0].label == 'Team "core" \\ 이름'
        assert got.token == "coingecko:aaa" and got.gecko_id == "aaa"
        assert got.points == 84 and len(got.events) == 2
        assert "description" not in got.events[0]  # 필요한 칸만

    def test_layout_changes_are_loud(self, tmp_path: Path) -> None:
        def write(text: str) -> Path:
            p = tmp_path / "x.json"
            p.write_text(text, "utf-8")
            return p

        ok = json.dumps(_schedule_doc(3), separators=(",", ":"))
        with pytest.raises(ScheduleFeedError, match="머리"):
            parse_schedule(write('{"metadata":{}}'), "x")
        with pytest.raises(ScheduleFeedError, match="tokenAllocation"):
            parse_schedule(write(ok.replace('],"tokenAllocation":', '],"other":')), "x")
        with pytest.raises(ScheduleFeedError, match="metadata"):
            parse_schedule(write(ok.replace('},"metadata":{', '},"meta":{')), "x")
        swapped = ok.replace('{"timestamp":', '{"t0":1,"timestamp":', 1)
        with pytest.raises(ScheduleFeedError, match="점 꼴"):
            parse_schedule(
                write(swapped), "x"
            )  # 칸 순서가 바뀌면 점을 못 뽑는다 — 조용히 넘기지 않음

    def test_protocol_list(self, tmp_path: Path) -> None:
        p = tmp_path / "l.json"
        p.write_text('["a","b"]', "utf-8")
        assert parse_protocol_list(p) == ("a", "b")
        for bad in ('{"message":"pay"}', '["a",1]', "<html>"):
            p.write_text(bad, "utf-8")
            with pytest.raises(ScheduleFeedError):
                parse_protocol_list(p)


# ── 코인 표 ─────────────────────────────────────────────────────────────────────────────
class TestCoins:
    def test_first_address_wins_and_symbols_upper(self, tmp_path: Path) -> None:
        rows: list[Any] = [
            {"id": "aaa", "symbol": "aaa", "platforms": {"eth": "0xAB", "sol": ""}},
            {"id": "bbb", "symbol": "bbb", "platforms": {"eth": "0xab"}},
            {"id": None, "symbol": "ccc", "platforms": {"bsc": "0xCC"}},
            "junk",
        ]
        p = tmp_path / "c.json"
        p.write_text(json.dumps(rows), "utf-8")
        got = parse_coin_list(p, min_coins=1)
        assert got.by_id == {"aaa": "AAA", "bbb": "BBB"}
        assert got.by_addr == {"0xab": "AAA", "0xcc": "CCC"} and got.coins == 3
        with pytest.raises(CoinListError, match="적다"):
            parse_coin_list(p, min_coins=10)

    def test_streaming_equals_whole_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from updown.marketdata.coingecko import coins as mod

        rows = [
            {"id": f"c{k}", "symbol": f"s{k % 7}", "platforms": {"eth": f"0x{k % 5:02x}"}}
            for k in range(300)
        ]
        p = tmp_path / "c.json"
        p.write_text(json.dumps(rows, indent=1), "utf-8")
        whole = parse_coin_list(p, min_coins=1)
        monkeypatch.setattr(mod, "READ_CHARS", 7)  # 덩이 사이에 코인이 걸리게
        assert parse_coin_list(p, min_coins=1) == whole and whole.coins == 300
        for bad in ('{"status": {"error_code": 429}}', '[{"id": "a"}, {"id"', "<html>"):
            p.write_text(bad, "utf-8")
            with pytest.raises(CoinListError):
                parse_coin_list(p, min_coins=1)


# ── 사건 셈 ─────────────────────────────────────────────────────────────────────────────
COINS = CoinList(by_id={"aaa": "AAA", "bbb": "", "ccc": "CCC"}, by_addr={"0xdd": "DDD"}, coins=3)


def _sec(points: Sequence[tuple[int, float]]) -> Section:
    from array import array

    return Section(
        label="s",
        ts=array("q", [a for a, _b in points]),
        released=array("d", [b for _a, b in points]),
    )


D = date(2026, 12, 10)  # 풀림 날
AT = _ts(D) - 2 * DAY  # 판단 시각(풀림 날 - days_before)


def _sched(events: list[dict[str, Any]], *, slug: str = "aaa") -> Schedule:
    return Schedule(
        slug=slug,
        sections=(
            _sec([(AT - DAY, 400.0), (AT, 999.0)]),  # 판단 시각 그 점은 안 든다(앞만)
            _sec([(AT - 2 * DAY, 100.0)]),
        ),
        events=tuple(events),
        token="coingecko:aaa",
        gecko_id=None,
        points=3,
    )


def _ev(day: date, n: Any, cat: str = "a", kind: str = "step", hour: int = 5) -> dict[str, Any]:
    return {
        "timestamp": _ts(day) + hour * 3600,
        "noOfTokens": n,
        "category": cat,
        "unlockType": kind,
    }


class TestEvents:
    def test_symbol_chain_order(self) -> None:
        assert coin_symbol("aaa", "ethereum:0xdd", COINS) == "AAA"  # gecko_id 먼저
        assert (
            coin_symbol("bbb", "ethereum:0xdd", COINS) is None
        )  # id 가 있으면 다음 길로 안 넘어감
        assert coin_symbol(None, "coingecko:ccc", COINS) == "CCC"
        assert coin_symbol(None, "ethereum:0xDD", COINS) == "DDD"  # 주소는 소문자로
        assert coin_symbol("zzz", "plain", COINS) is None
        names = rank_names(["AAAUSDT", "10CCCUSDT", "XXDDDUSDT", "EEEBUSD"], RULE)
        assert names == {"AAAUSDT", "10CCCUSDT"}
        assert rank_symbol("CCC", names, RULE) == "10CCCUSDT"
        assert rank_symbol("DDD", names, RULE) is None and rank_symbol(None, names, RULE) is None

    def test_supply_before_uses_only_earlier_points(self) -> None:
        secs = _sched([]).sections
        assert supply_before(secs, AT) == 500.0
        assert supply_before(secs, AT + 1) == 1099.0
        assert supply_before(secs, AT - 2 * DAY) == 0.0

    def test_same_day_sum_filters_and_size(self) -> None:
        events = [
            _ev(D, [6]),
            _ev(D, [4.0, 9], cat="b", hour=20),  # 같은 UTC 날 · 첫 칸만
            _ev(D, [50], cat="other"),  # 범주 밖
            _ev(D, [50], kind="ramp"),  # 꼴 밖
            _ev(D, [0]),  # 0 은 사건 아님
            _ev(D, None),  # 토큰 수 없음 = 0
            {"timestamp": "x", "noOfTokens": [5], "category": "a", "unlockType": "step"},
            _ev(D + timedelta(days=1), [5]),
        ]
        got = day_events(_sched(events), "AAAUSDT", RULE, D, D + timedelta(days=1))
        assert [(e.day, e.tokens, e.categories) for e in got] == [
            (D, 10.0, ("a", "b")),
            (D + timedelta(days=1), 5.0, ("a",)),
        ]
        first = got[0]
        assert first.supply == 500.0 and first.size_pct == 10.0 / 500.0 * 100
        at_gate = replace(RULE, min_size_pct=first.size_pct)
        assert passes(first, at_gate)  # 같으면 지남
        assert not passes(first, replace(RULE, min_size_pct=first.size_pct + 1e-9))
        assert first.key == f"aaa@{D.isoformat()}"
        assert RULE.entry_at(D) == datetime(2026, 12, 8, tzinfo=UTC)
        assert not passes(replace(first, size_pct=1.49), RULE)
        assert (
            day_events(
                _sched(events), "AAAUSDT", RULE, D + timedelta(days=2), D + timedelta(days=9)
            )
            == []
        )
        assert SupplyEvent.from_json(first.to_json()) == first

    def test_no_supply_means_no_event(self) -> None:
        empty = replace(_sched([_ev(D, [5])]), sections=())
        assert day_events(empty, "AAAUSDT", RULE, D, D) == []

    def test_config_loader(self, tmp_path: Path) -> None:
        ok = tmp_path / "a.yml"
        ok.write_text(
            "supply_events:\n  kind_x:\n    source: S\n    categories: [a]\n"
            "    release_type: step\n"
            "    days_before: 2\n    min_size_pct: '1.5'\n    quote: USDT\n    prefixes: ['']\n"
            "    exclude_prefix: []\n    rank_source: R\n    rank_top: 50\n    rank_days: 7\n"
            "    rank_min_age_days: 9\n"
            "supply_job:\n  stale_h: 30\n  horizon_days: 7\n",
            encoding="utf-8",
        )
        got = load_supply_rules(ok)["kind_x"]
        assert got.min_size_pct == 1.5 and got.categories == {"a"} and got.rank_top == 50
        assert got.prefixes == ("",) and got.exclude_prefix == () and got.rank_days == 7
        cfg = load_supply_job_config(ok)
        assert (cfg.stale_h, cfg.horizon_days) == (30, 7)
        bad = tmp_path / "b.yml"
        bad.write_text("supply_events:\n  kind_x:\n    source: S\n    what: 1\n", encoding="utf-8")
        with pytest.raises(AnnouncementConfigError, match="모르는 칸"):
            load_supply_rules(bad)
        # 🔴 매매에 닿는 칸은 필수 — 빠지면 기본값으로 채우지 않고 터뜨린다
        full = ok.read_text(encoding="utf-8").split("supply_job:")[0]
        for key in ("prefixes", "exclude_prefix", "rank_top", "quote", "rank_source"):
            short = tmp_path / f"c_{key}.yml"
            short.write_text(
                "\n".join(x for x in full.splitlines() if not x.strip().startswith(key + ":")),
                encoding="utf-8",
            )
            with pytest.raises(AnnouncementConfigError, match="빠졌다"):
                load_supply_rules(short)
        typo = tmp_path / "t.yml"
        typo.write_text("supply_job:\n  stale_hours: 30\n", encoding="utf-8")
        with pytest.raises(AnnouncementConfigError, match="모르는 칸"):
            load_supply_job_config(typo)
        empty = tmp_path / "d.yml"
        empty.write_text("kinds: {}\n", encoding="utf-8")
        assert load_supply_rules(empty) == {}
        with pytest.raises(AnnouncementConfigError):
            SupplyJobConfig(stale_h=12)  # 하루 한 번 받는데 12 시간이면 늘 모름


# ── 큰 파일 흘려 받기(로컬 HTTP) ───────────────────────────────────────────────────────────
@dataclass
class _Reply:
    status: int
    body: bytes
    ctype: str = "application/json"
    gzip: bool = False


@pytest.fixture
def server() -> Iterator[tuple[str, dict[str, list[_Reply]]]]:
    routes: dict[str, list[_Reply]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            queue = routes.get(self.path) or [_Reply(404, b"no")]
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
            body = gzip.compress(reply.body) if reply.gzip else reply.body
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.ctype)
            if reply.gzip:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("ETag", 'W/"e1"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", routes
    finally:
        srv.shutdown()
        srv.server_close()


class TestFetch:
    def test_gzip_stream_and_failures(
        self, tmp_path: Path, server: tuple[str, dict[str, list[_Reply]]]
    ) -> None:
        base, routes = server
        big = json.dumps(["x" * 50] * 20000).encode()
        routes["/ok"] = [_Reply(200, big, gzip=True)]
        routes["/html"] = [_Reply(200, b"<html>pay us</html>", ctype="text/html")]
        routes["/gone"] = [_Reply(404, b"nope")]
        routes["/flaky"] = [_Reply(503, b"busy"), _Reply(200, b"[]")]
        dst = tmp_path / "f.json"
        info = fetch_to(f"{base}/ok", dst, timeout_s=10, backoff_s=0)
        assert dst.read_bytes() == big and info.bytes == len(big) and info.wire_bytes < len(big)
        assert info.etag == 'W/"e1"' and info.status == 200
        with pytest.raises(FileFetchError, match="JSON 아님"):
            fetch_to(f"{base}/html", dst, timeout_s=10, backoff_s=0)
        with pytest.raises(FileFetchError) as gone:
            fetch_to(f"{base}/gone", dst, timeout_s=10, tries=3, backoff_s=0)
        assert gone.value.status == 404  # 4xx 는 다시 묻지 않는다
        assert fetch_to(f"{base}/flaky", dst, timeout_s=10, backoff_s=0).status == 200

    def test_emissions_source_paths(
        self, tmp_path: Path, server: tuple[str, dict[str, list[_Reply]]]
    ) -> None:
        base, routes = server
        routes["/emissionsProtocolsList"] = [_Reply(200, b'["aaa"]')]
        doc = json.dumps(_schedule_doc(5), separators=(",", ":")).encode()
        routes["/emissions/aaa"] = [_Reply(200, doc, gzip=True)]
        src = emissions.EmissionsSource(base=base, pace_s=0, tries=1)
        names, _info = src.protocol_list(tmp_path)
        assert names == ("aaa",)
        sch, info = src.schedule("aaa", tmp_path)
        assert sch.gecko_id == "aaa" and info.etag == 'W/"e1"'
        assert list(tmp_path.iterdir()) == []  # 받은 원본은 바로 지운다
        with pytest.raises(ScheduleFeedError):
            src.schedule("zzz", tmp_path)
        assert src.calls == 3


# ── 받기 프로세스 본문 ───────────────────────────────────────────────────────────────────
def _info(etag: str = "e") -> FetchInfo:
    return FetchInfo("u", 200, "application/json", etag, "lm", 10, 100, 0.1)


class _Src:
    def __init__(self, files: dict[str, Schedule | None], *, list_fail: bool = False) -> None:
        self.files = files
        self.list_fail = list_fail
        self.calls = 0
        self.wire_bytes = 0

    def protocol_list(self, tmp: Path) -> tuple[tuple[str, ...], FetchInfo]:  # noqa: ARG002
        self.calls += 1
        if self.list_fail:
            raise ScheduleFeedError("막힘")
        return tuple(self.files), _info()

    def schedule(self, slug: str, tmp: Path) -> tuple[Schedule, FetchInfo]:  # noqa: ARG002
        self.calls += 1
        if slug == "boom":
            raise RuntimeError("뜻밖의 예외(끊김 등)")
        got = self.files[slug]
        if got is None:
            raise ScheduleFeedError("응답 403")
        return got, _info(f"e-{slug}")


class _Coins:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def coin_list(self, tmp: Path) -> tuple[CoinList, FetchInfo]:  # noqa: ARG002
        if self.fail:
            raise CoinListError("429")
        return COINS, _info()


class TestFetchProcessBody:
    PAYLOAD: Mapping[str, Any] = {
        "asof": "2026-12-01T01:23:00+00:00",
        "horizon_days": 10,
        "kinds": ["kind_x"],
        "names": ["AAAUSDT", "10CCCUSDT"],
        "pace_s": 0,
    }

    def test_events_and_failed_files(self, tmp_path: Path) -> None:
        files: dict[str, Schedule | None] = {
            "aaa": _sched([_ev(D, [10]), _ev(D + timedelta(days=30), [10])]),
            "ccc": replace(_sched([_ev(D, [1])], slug="ccc"), token="coingecko:ccc"),
            "zzz": replace(_sched([_ev(D, [9])], slug="zzz"), token="plain"),  # 짝 없음
            "bad": None,
            "boom": None,  # 원천이 뜻밖의 예외를 던진다 — 적고 계속한다
        }
        got = supply_fetch.run(
            self.PAYLOAD,
            schedule_source=lambda _s, _p: _Src(files),
            coin_source=_Coins,
            rules={"kind_x": RULE},
            tmp=tmp_path,
        )
        assert got["ok"] is True
        src = got["sources"]["SRC"]
        assert src["list_n"] == 5 and src["files_ok"] == 3
        assert [f[0] for f in src["failed"]] == ["bad", "boom"]
        assert "RuntimeError" in src["failed"][1][1]
        kind = got["kinds"]["kind_x"]
        assert kind["mapped"] == 2
        assert kind["kind_lines"] == 4  # 꼴 · 범주 줄(짝 없는 프로젝트 · 창 밖 날도 센다)
        rows = {(e["slug"], e["day"]): e for e in kind["events"]}
        assert set(rows) == {("aaa", D.isoformat()), ("ccc", D.isoformat())}  # 창 밖 날은 뺌
        assert rows[("aaa", D.isoformat())]["etag"] == "e-aaa"
        assert rows[("ccc", D.isoformat())]["symbol"] == "10CCCUSDT"

    def test_list_or_coin_failure_is_fatal(self, tmp_path: Path) -> None:
        for src, coins in ((_Src({}, list_fail=True), _Coins()), (_Src({}), _Coins(fail=True))):
            got = supply_fetch.run(
                self.PAYLOAD,
                schedule_source=lambda _s, _p, src=src: src,
                coin_source=lambda coins=coins: coins,
                rules={"kind_x": RULE},
                tmp=tmp_path,
            )
            assert got["ok"] is False and got["fatal"]


# ── 작업 ────────────────────────────────────────────────────────────────────────────────
class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], EventRow] = {}
        self.feeds: dict[str, FeedState] = {}
        self.marks: list[dict[str, Any]] = []

    async def feed(self, kind: str) -> FeedState | None:
        return self.feeds.get(kind)

    async def add(self, rows: Sequence[EventRow]) -> int:
        n = 0
        for r in rows:
            if (r.kind, r.token) not in self.rows:
                self.rows[(r.kind, r.token)] = r
                n += 1
        return n

    async def pending(self, kind: str, reasons: Collection[str], after: datetime) -> list[EventRow]:
        return sorted(
            (
                r
                for (k, _t), r in self.rows.items()
                if k == kind
                and r.contract is None
                and r.reason in reasons
                and r.announced_at > after
            ),
            key=lambda r: r.announced_at,
        )

    async def put_pending(self, rows: Sequence[EventRow], reasons: Collection[str]) -> int:
        n = 0
        for r in rows:
            old = self.rows.get((r.kind, r.token))
            if old is None or (old.contract is None and old.reason in reasons):
                self.rows[(r.kind, r.token)] = r
                n += 1
        return n

    async def waiting(
        self, kind: str, reason: str, after: datetime, until: datetime
    ) -> list[EventRow]:
        return sorted(
            (
                r
                for (k, _t), r in self.rows.items()
                if k == kind
                and r.contract is None
                and r.reason == reason
                and after < r.announced_at <= until
            ),
            key=lambda r: r.announced_at,
        )

    async def settle(self, rows: Sequence[EventRow]) -> int:
        for r in rows:
            if self.rows[(r.kind, r.token)].contract is None:
                self.rows[(r.kind, r.token)] = r
        return len(rows)

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
            stats=dict(stats) if stats is not None else dict(old.stats),
        )
        self.marks.append({"kind": kind, "ok": ok, "error": error})


def _result(
    events: list[SupplyEvent],
    *,
    list_n: int = 3,
    failed: list[list[str]] | None = None,
    mapped: int = 2,
    lines: int = 5,
) -> dict[str, Any]:
    return {
        "ok": True,
        "sources": {
            "SRC": {
                "list_n": list_n,
                "files_ok": list_n - len(failed or []),
                "failed": failed or [],
                "calls": list_n + 1,
                "wire_bytes": 1000,
            }
        },
        "kinds": {
            "kind_x": {
                "mapped": mapped,
                "kind_lines": lines,
                "events": [{**e.to_json(), "etag": "W/1"} for e in events],
            }
        },
        "seconds": 1.0,
        "peak_rss_kb": 1,
    }


def _sev(slug: str, day: date, size: float, symbol: str = "AAAUSDT") -> SupplyEvent:
    return SupplyEvent(slug, symbol, day, size, 100.0, size, ("a",))


class _Fetch:
    def __init__(self, results: list[Mapping[str, Any] | Exception]) -> None:
        self.results = results
        self.payloads: list[Mapping[str, Any]] = []

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.payloads.append(payload)
        got = self.results.pop(0)
        if isinstance(got, Exception):
            raise got
        return got


async def _names() -> list[str]:
    return ["AAAUSDT", "BBBUSDT"]


class TestRefresh:
    def _run(self, store: FakeStore, fetch: _Fetch, now: datetime = NOW) -> Any:
        return asyncio.run(
            run_supply_events(
                store,
                [RULE],
                fetch=fetch,
                names=_names,
                members=None,
                mapper=None,
                now=now,
                cfg=CFG,
            )
        )

    def test_writes_seen_rows_and_pending_rows(self) -> None:
        store = FakeStore()
        d_far = NOW.date() + timedelta(days=8)
        d_near = NOW.date() + timedelta(days=2)  # 진입(D - 3)이 이미 지남 — 기록만
        fetch = _Fetch(
            [_result([_sev("aaa", d_far, 2.5), _sev("bbb", d_far, 1.0), _sev("aaa", d_near, 9.0)])]
        )
        (out,) = self._run(store, fetch)
        assert out.refreshed and not out.refresh_error
        assert fetch.payloads[0]["names"] == ["AAAUSDT", "BBBUSDT"] and fetch.payloads[0][
            "kinds"
        ] == ["kind_x"]
        main = {t: r for (k, t), r in store.rows.items() if k == "kind_x"}
        assert {t: r.reason for t, r in main.items()} == {
            f"aaa@{d_far}": "listed",
            f"bbb@{d_far}": "small",
        }
        row = main[f"aaa@{d_far}"]
        assert row.announced_at == RULE.entry_at(d_far) and row.contract is None
        assert row.rank_symbol == "AAAUSDT" and row.article_id == "W/1" and row.seen_at == NOW
        seen = [r for (k, _t), r in store.rows.items() if k == "kind_x" + SEEN_SUFFIX]
        assert len(seen) == 3 and all(r.token.endswith(f"@{NOW.date()}") for r in seen)
        assert {r.reason for r in seen} == {"seen", "seen_small"}
        feed = store.feeds["kind_x"]
        assert feed.ok_at == NOW and feed.backfilled_at == NOW and feed.stats["list_n"] == 3

    def test_next_day_updates_and_drops_but_keeps_failed_files(self) -> None:
        store = FakeStore()
        d1 = NOW.date() + timedelta(days=8)
        self._run(store, _Fetch([_result([_sev("aaa", d1, 2.5), _sev("ccc", d1, 3.0, "CCCUSDT")])]))
        # 같은 날 다시 돌아도(받기 시각 뒤 · 이미 성공) 받지 않는다
        fetch_same = _Fetch([])
        self._run(store, fetch_same, NOW + H)
        assert fetch_same.payloads == []
        tomorrow = NOW + 24 * H
        later = _Fetch(
            [
                _result([_sev("aaa", d1, 1.2)], failed=[["ccc", "응답 403"]])
            ]  # aaa 작아짐 · ccc 못 받음
        )
        self._run(store, later, tomorrow)
        main = {t: r for (k, t), r in store.rows.items() if k == "kind_x"}
        assert main[f"aaa@{d1}"].reason == "small" and main[f"aaa@{d1}"].seen_at == tomorrow
        assert (
            main[f"ccc@{d1}"].reason == "listed" and main[f"ccc@{d1}"].seen_at == NOW
        )  # 어제 값 그대로
        # 그다음 날 ccc 를 받았는데 그 날 사건이 없다 → dropped · 다시 나타나면 listed
        self._run(store, _Fetch([_result([_sev("aaa", d1, 2.1)])]), tomorrow + 24 * H)
        main = {t: r for (k, t), r in store.rows.items() if k == "kind_x"}
        assert main[f"ccc@{d1}"].reason == "dropped" and main[f"aaa@{d1}"].reason == "listed"
        self._run(store, _Fetch([_result([_sev("ccc", d1, 3.0, "CCCUSDT")])]), tomorrow + 48 * H)
        main = {t: r for (k, t), r in store.rows.items() if k == "kind_x"}
        assert main[f"ccc@{d1}"].reason == "listed"

    def test_feed_failures_are_recorded_and_known_expires(self) -> None:
        store = FakeStore()
        self._run(store, _Fetch([_result([], list_n=10)]))
        cases: list[Mapping[str, Any] | Exception] = [
            _result([], list_n=7),  # 10 → 7 급감
            _result([], list_n=10, failed=[["a", "x"], ["b", "x"], ["c", "x"], ["d", "x"]]),
            {"ok": False, "fatal": "DEFILLAMA 목록: 응답 402"},
            SupplyFeedError("받기 프로세스 끝 코드 1"),
        ]
        for k, case in enumerate(cases, start=1):
            (out,) = self._run(store, _Fetch([case]), NOW + 24 * H * k)
            assert not out.refreshed and out.refresh_error, case
            assert store.feeds["kind_x"].ok_at == NOW  # 마지막 성공은 그대로
        assert store.marks[-1]["ok"] is False and "끝 코드" in str(store.marks[-1]["error"])
        feed = store.feeds["kind_x"]
        assert feeds_known({"kind_x": feed}, ["kind_x"], NOW + 47 * H, 900, {"kind_x": 48 * 3600})
        assert not feeds_known(
            {"kind_x": feed}, ["kind_x"], NOW + 49 * H, 900, {"kind_x": 48 * 3600}
        )

    def test_refresh_due(self) -> None:
        assert refresh_due(None, NOW, CFG)
        fresh = FeedState("kind_x", backfilled_at=NOW, ok_at=NOW)
        assert not refresh_due(fresh, NOW + 22 * H, CFG)  # 다음 날 01:00 전
        assert refresh_due(fresh, NOW.replace(hour=1, minute=0) + 24 * H, CFG)
        early = FeedState("kind_x", backfilled_at=NOW, ok_at=NOW.replace(hour=0, minute=40))
        assert refresh_due(early, NOW, CFG)  # 그날 받기 시각 앞에 받은 것은 그날 몫이 아니다
        failed = FeedState("kind_x", backfilled_at=NOW, ok_at=NOW - 30 * H, error_at=NOW, error="x")
        assert not refresh_due(
            failed, NOW + H, CFG
        )  # 실패 뒤 1.5 시간 안은 다시 안 받는다(두 시간마다)
        assert refresh_due(failed, NOW + 2 * H, CFG)
        paired = replace(failed, error="pair: 봉 못 받음")
        assert refresh_due(paired, NOW + H, CFG)  # 짝짓기 실패는 받기를 미루지 않는다


# ── 짝짓기 · 순위 문 ─────────────────────────────────────────────────────────────────────
EVT = datetime(2026, 11, 29, 0, tzinfo=UTC)  # 진입 봉 시작
PAIR_NOW = EVT - timedelta(hours=3, minutes=37)  # 20:23


def _row(token: str, rank: str, *, seen: datetime, at: datetime = EVT) -> EventRow:
    return EventRow("kind_x", token, at, seen, "W/1", "t", rank, None, "listed")


class TestPairing:
    def _run(self, store: FakeStore, members: Any, mapper: Any, now: datetime = PAIR_NOW) -> Any:
        return asyncio.run(
            run_supply_events(
                store,
                [RULE],
                fetch=None,
                names=None,
                members=members,
                mapper=mapper,
                now=now,
                cfg=CFG,
            )
        )

    def test_stale_rank_and_contract(self) -> None:
        store = FakeStore()
        for row in (
            _row("aaa@d", "AAAUSDT", seen=PAIR_NOW - 20 * H),
            _row("bbb@d", "BBBUSDT", seen=PAIR_NOW - 20 * H),  # 순위 밖
            _row("ccc@d", "CCCUSDT", seen=PAIR_NOW - 49 * H),  # 마지막 확인이 오래됨
            _row("ddd@d", "DDDUSDT", seen=PAIR_NOW - 1 * H, at=EVT + 24 * H),  # 아직 창 밖
        ):
            store.rows[(row.kind, row.token)] = row
        asked: list[tuple[date, datetime]] = []
        mapped: list[Sequence[str]] = []

        async def members(_rule: SupplyRule, day: date, now: datetime) -> frozenset[str]:
            asked.append((day, now))
            return frozenset({"AAAUSDT", "CCCUSDT"})

        async def mapper(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            mapped.append(list(tokens))
            return {t: (t, t.removesuffix("USDT") + "_USDT", "ok") for t in tokens}

        (out,) = self._run(store, members, mapper)
        assert out.paired == 1 and not out.pair_error
        rows = {t: r for (_k, t), r in store.rows.items()}
        assert rows["aaa@d"].contract == "AAA_USDT" and rows["aaa@d"].seen_at == PAIR_NOW
        assert (rows["bbb@d"].reason, rows["bbb@d"].contract) == ("outside_top", None)
        assert rows["ccc@d"].reason == "listed"  # 오래됐지만 진입 앞 마지막 점검까지는 둔다
        assert rows["ddd@d"].reason == "listed"
        assert asked == [(EVT.date(), PAIR_NOW)] and mapped == [["AAAUSDT"]]
        # 다시 점검해도 정한 줄은 그대로 · 묻지 않는다
        self._run(store, members, mapper, PAIR_NOW + H)
        assert len(asked) == 1 and len(mapped) == 1
        # 마지막 점검(진입 37 분 앞)에서도 오래됐으면 stale 로 정한다
        self._run(store, members, mapper, PAIR_NOW + 3 * H)
        assert store.rows["kind_x", "ccc@d"].reason == "stale" and len(mapped) == 1

    def test_rank_failure_keeps_rows_waiting(self) -> None:
        store = FakeStore()
        row = _row("aaa@d", "AAAUSDT", seen=PAIR_NOW - 20 * H)
        store.rows[(row.kind, row.token)] = row

        async def members(_rule: SupplyRule, _day: date, _now: datetime) -> frozenset[str]:
            raise RuntimeError("봉 못 받음")

        async def mapper(
            _tokens: Sequence[str],
        ) -> Mapping[str, tuple[str | None, str | None, str]]:
            raise AssertionError("순위 문 전에는 짝짓기를 안 한다")

        (out,) = self._run(store, members, mapper)
        assert "봉 못 받음" in out.pair_error and out.paired == 0
        assert store.rows[("kind_x", "aaa@d")].reason == "listed"
        assert store.marks[-1]["ok"] is False


class _RankSrc:
    def __init__(
        self,
        bars: dict[str, list[tuple[datetime, Decimal, Decimal]]],
        listed: dict[str, date | None],
    ) -> None:
        self.bars = bars
        self.listed = listed
        self.asked: list[tuple[str, datetime, datetime]] = []

    async def listed_symbols(self) -> dict[str, date | None]:
        return self.listed

    async def hourly_rows(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, Decimal, Decimal]]:
        self.asked.append((symbol, start, end))
        if symbol == "BOOMUSDT":
            raise RuntimeError("막힘")
        return [b for b in self.bars.get(symbol, []) if start <= b[0] < end]


class TestRankMembers:
    def test_window_age_and_partial_last_day(self) -> None:
        day = date(2026, 11, 29)
        mid = datetime(2026, 11, 29, tzinfo=UTC)

        def bars(amount: str, *hours: int) -> list[tuple[datetime, Decimal, Decimal]]:
            return [(mid - timedelta(hours=h), Decimal(amount), Decimal(1)) for h in hours]

        src = _RankSrc(
            bars={
                "AAAUSDT": bars("10", 30, 50),  # 창 안 20
                "BBBUSDT": bars("15", 30)
                + bars("100", 2),  # 마지막 날 22:00 봉 — 지금(20:23)엔 아직 없음
                "CCCUSDT": bars("50", 30),  # 상장이 짧다(나이 5 일 이하)
                "DDDUSDT": bars("12", 80),  # 창(3 일) 밖
                "XXEUSDT": bars("99", 30),  # 빼는 앞붙이
                "FFFUSDT": bars("1", 30),
            },
            listed={
                "AAAUSDT": date(2026, 1, 1),
                "BBBUSDT": date(2026, 1, 1),
                "CCCUSDT": date(2026, 11, 25),
                "DDDUSDT": date(2026, 1, 1),
                "XXEUSDT": date(2026, 1, 1),
                "FFFUSDT": date(2026, 1, 1),
                "GGGUSDT": None,
            },
        )
        now = mid - timedelta(hours=3, minutes=37)
        got = asyncio.run(rank_members(src, RULE, day, now))
        assert got == {"AAAUSDT", "BBBUSDT"}  # 20 · 15 (BBB 의 22:00 봉은 안 셈) · 위 2
        names = {a for a, _s, _e in src.asked}
        assert "XXEUSDT" not in names
        assert all(
            s == mid - timedelta(days=3) and e == mid - timedelta(hours=4) for _a, s, e in src.asked
        )
        src.listed["BOOMUSDT"] = date(2026, 1, 1)
        with pytest.raises(RuntimeError, match="막힘"):  # 🔴 부분 자료로 순위를 내지 않는다
            asyncio.run(rank_members(src, RULE, day, now))


# ── 받기 프로세스 띄우기 · 표 쓰기 SQL ─────────────────────────────────────────────────────
class TestSpawnAndStore:
    def test_spawn_round_trip_and_failures(self) -> None:
        echo = (
            "import json,sys; d=json.load(sys.stdin); print('log'); "
            "print(json.dumps({'ok': True, 'got': d['x']}))"
        )
        got = asyncio.run(spawn_fetch([sys.executable, "-c", echo], {"x": 7}, timeout_s=30))
        assert got == {"ok": True, "got": 7}
        with pytest.raises(SupplyFeedError, match="끝 코드 3"):
            asyncio.run(
                spawn_fetch([sys.executable, "-c", "import sys; sys.exit(3)"], {}, timeout_s=30)
            )
        with pytest.raises(SupplyFeedError, match="시간 초과"):
            asyncio.run(
                spawn_fetch([sys.executable, "-c", "import time; time.sleep(5)"], {}, timeout_s=0.5)
            )
        with pytest.raises(SupplyFeedError, match="JSON"):
            asyncio.run(spawn_fetch([sys.executable, "-c", "print('{oops')"], {}, timeout_s=30))

    def test_put_pending_only_rewrites_undecided_rows(self) -> None:
        """넣거나 고치는 SQL — 짝을 지었거나 결과를 적은 줄은 안 고친다(WHERE 절)."""
        import sqlalchemy as sa
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from updown.common.db.models.market import AnnouncementEvent

        stmt = pg_insert(AnnouncementEvent).values(
            [
                {
                    "kind": "kind_x",
                    "token": "t",
                    "announced_at": NOW,
                    "seen_at": NOW,
                    "article_id": "",
                    "title": "",
                    "rank_symbol": None,
                    "contract": None,
                    "reason": "listed",
                }
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["kind", "token"],
            set_={"reason": stmt.excluded.reason},
            where=sa.and_(
                AnnouncementEvent.contract.is_(None),
                AnnouncementEvent.reason.in_(sorted(PENDING)),
            ),
        )
        sql = str(stmt.compile(dialect=postgresql.dialect()))  # pyright: ignore[reportUnknownMemberType]
        assert "ON CONFLICT (kind, token) DO UPDATE" in sql
        assert "WHERE announcement_events.contract IS NULL AND announcement_events.reason IN" in sql
        assert {"listed", "small", "dropped"} == PENDING


# ── 검토 반영(T1057 · 11-28): 끊김 · 조용한 0 · 메모리 문 · 최소 환경 · 오래됨 · 차례 ─────────────
def _cut_server(body: bytes, *, declared: int, gz: bool = False) -> tuple[str, Any]:
    """머리에 적은 길이보다 적게 보내고 끊는 서버(도중 끊김 재현)."""
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve() -> None:
        for _ in range(4):
            try:
                conn, _addr = srv.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                head = (
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + ("Content-Encoding: gzip\r\n" if gz else "")
                    + f"Content-Length: {declared}\r\nConnection: close\r\n\r\n"
                )
                conn.sendall(head.encode() + body)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}/x", srv


class TestCutTransfer:
    def test_short_body_is_a_retryable_transfer_failure(self, tmp_path: Path) -> None:
        url, srv = _cut_server(b'["abc", "de', declared=200)
        try:
            with pytest.raises(FileFetchError, match="전송 실패") as err:
                fetch_to(url, tmp_path / "f.json", timeout_s=5, tries=2, backoff_s=0)
            assert err.value.status is None  # 상태 없음 = 다시 물을 만한 실패
        finally:
            srv.close()

    def test_gzip_cut_before_its_end_is_a_failure(self, tmp_path: Path) -> None:
        whole = gzip.compress(json.dumps(["x" * 40] * 2000).encode())
        part = whole[: len(whole) // 2]
        url, srv = _cut_server(part, declared=len(part), gz=True)  # 길이는 맞는데 gzip 이 안 끝남
        try:
            with pytest.raises(FileFetchError, match="gzip 끝"):
                fetch_to(url, tmp_path / "f.json", timeout_s=5, tries=1, backoff_s=0)
        finally:
            srv.close()

    def test_broken_numbers_are_a_schedule_failure(self, tmp_path: Path) -> None:
        doc = json.dumps(_schedule_doc(3), separators=(",", ":"))
        p = tmp_path / "x.json"
        p.write_text(doc.replace('"unlocked":10.0', '"unlocked":1e', 1), "utf-8")
        with pytest.raises(ScheduleFeedError, match="못 푼다"):
            parse_schedule(p, "x")


class TestQuietZeroAndGuards:
    def _run(self, store: FakeStore, fetch: _Fetch, now: datetime = NOW, **kw: Any) -> Any:
        return asyncio.run(
            run_supply_events(
                store,
                [RULE],
                fetch=fetch,
                names=_names,
                members=kw.get("members"),
                mapper=kw.get("mapper"),
                now=now,
                cfg=CFG,
                headroom=kw.get("headroom"),
            )
        )

    def test_zero_or_collapsed_kind_lines_and_mapped_fail(self) -> None:
        store = FakeStore()
        self._run(store, _Fetch([_result([], lines=50, mapped=20)]))
        assert store.feeds["kind_x"].stats["kind_lines"] == 50
        for k, case in enumerate(
            (
                _result([], lines=0, mapped=20),  # 꼴 · 범주 줄 0 — 자료 칸이 바뀌었나
                _result([], lines=50, mapped=0),  # 짝 0 — 코인 목록 꼴이 바뀌었나
                _result([], lines=30, mapped=20),  # 50 → 30 급감
                _result([], lines=50, mapped=10),  # 20 → 10 급감
            ),
            start=1,
        ):
            (out,) = self._run(store, _Fetch([case]), NOW + 24 * H * k)
            assert not out.refreshed and ("0" in out.refresh_error or "급감" in out.refresh_error)
        assert store.feeds["kind_x"].ok_at == NOW

    def test_memory_gate_skips_the_spawn_and_retries_next_check(self) -> None:
        store = FakeStore()
        fetch = _Fetch([_result([])])
        (out,) = self._run(store, fetch, headroom=lambda: 80.0)
        assert fetch.payloads == [] and not out.refreshed
        assert out.refresh_error.startswith("memory:")
        assert store.feeds["kind_x"].error is not None
        # 메모리 문은 다시 받기 간격(retry_h)을 걸지 않는다 — 다음 점검(한 시간 뒤)에 바로 다시
        (out,) = self._run(store, fetch, NOW + H, headroom=lambda: 500.0)
        assert out.refreshed and len(fetch.payloads) == 1
        # 한도를 모르면(None) 본다고 치지 않고 띄운다
        store2, fetch2 = FakeStore(), _Fetch([_result([])])
        (out2,) = self._run(store2, fetch2, headroom=lambda: None)
        assert out2.refreshed

    def test_headroom_and_minimal_env(self) -> None:
        from updown.orchestration.rebalancer.supply_job import fetch_env, memory_headroom_mb

        mib = 1 << 20
        assert memory_headroom_mb({"current": 300 * mib, "max": 512 * mib}) == 212.0
        assert memory_headroom_mb({"current": None, "max": None}) is None
        env = fetch_env(
            {"PATH": "/bin", "EXCHANGE_SECRET": "s", "DATABASE_URL": "pg://", "LANG": "C"}
        )
        assert env == {"PATH": "/bin", "LANG": "C"}
        show = (
            "import json, os, sys; json.load(sys.stdin); "
            "print(json.dumps({'keys': sorted(os.environ)}))"
        )
        got = asyncio.run(
            spawn_fetch(
                [sys.executable, "-c", show], {}, timeout_s=30, env={"PATH": "/usr/bin:/bin"}
            )
        )
        keys = set(cast("list[str]", got["keys"]))
        assert "EXCHANGE_SECRET" not in keys and "DATABASE_URL" not in keys

    def test_spawned_child_lowers_itself(self) -> None:
        code = (
            "import json, os, sys; from updown.apps.supply_fetch import lower_priority; "
            "json.load(sys.stdin); lower_priority(); "
            "adj = open('/proc/self/oom_score_adj').read().strip() "
            "if os.path.exists('/proc/self/oom_score_adj') else '1000'; "
            "print(json.dumps({'adj': adj, 'nice': os.nice(0)}))"
        )
        env = dict(fetch_env_all())
        got = asyncio.run(spawn_fetch([sys.executable, "-c", code], {}, timeout_s=30, env=env))
        assert got["adj"] == "1000" and got["nice"] >= 10

    def test_stale_waits_for_the_last_chance_and_refresh_runs_first(self) -> None:
        store = FakeStore()
        d = (EVT + 2 * 86400 * timedelta(seconds=1)).date()  # 진입 EVT 의 풀림 날(days_before 2)
        old = _row(f"aaa@{d.isoformat()}", "AAAUSDT", seen=PAIR_NOW - 49 * H)
        store.rows[(old.kind, old.token)] = old
        store.feeds["kind_x"] = FeedState("kind_x", backfilled_at=NOW, ok_at=PAIR_NOW - 49 * H)

        async def members(_rule: SupplyRule, _day: date, _now: datetime) -> frozenset[str]:
            return frozenset({"AAAUSDT"})

        async def mapper(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            return {t: (t, t.removesuffix("USDT") + "_USDT", "ok") for t in tokens}

        # 20:23 — 오래됐지만 아직 마지막 점검이 아니다(받기도 못 함) → listed 로 둔다
        (out,) = self._run(
            store, _Fetch([SupplyFeedError("막힘")]), PAIR_NOW, members=members, mapper=mapper
        )
        assert store.rows[old.kind, old.token].reason == "listed" and out.paired == 0
        # 22:23 — 받기가 그 사건을 다시 확인하고(먼저) 같은 점검의 짝짓기가 짓는다
        again = _sev("aaa", d, 2.5)
        (out,) = self._run(
            store, _Fetch([_result([again])]), PAIR_NOW + 2 * H, members=members, mapper=mapper
        )
        assert out.refreshed and out.paired == 1
        assert store.rows[old.kind, old.token].contract == "AAA_USDT"

    def test_stale_is_settled_only_on_the_last_check(self) -> None:
        store = FakeStore()
        row = _row("bbb@d", "BBBUSDT", seen=PAIR_NOW - 49 * H)
        store.rows[(row.kind, row.token)] = row

        async def mapper(
            _tokens: Sequence[str],
        ) -> Mapping[str, tuple[str | None, str | None, str]]:
            raise AssertionError("오래된 줄은 짝짓지 않는다")

        for hours, want in ((0, "listed"), (2, "listed"), (3, "stale")):  # 20:23 · 22:23 · 23:23
            self._run(store, _Fetch([]), PAIR_NOW + hours * H, mapper=mapper, members=None)
            assert store.rows[row.kind, row.token].reason == want, hours


def fetch_env_all() -> dict[str, str]:
    import os

    from updown.orchestration.rebalancer.supply_job import fetch_env

    return fetch_env(os.environ)


class TestRegistration:
    def test_broken_sub_job_does_not_block_the_title_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """하위 작업 설정이 깨지거나 등록이 터져도 공지 작업은 등록된다(깨진 갈래만 빠지고 오류)."""
        from types import SimpleNamespace

        import updown.analysis.announcements as an
        import updown.analysis.listings as li
        import updown.analysis.playbook.select as sel
        import updown.analysis.supply_schedule as ss
        import updown.decision.universe_names as un
        import updown.orchestration.rebalancer.announce_job as aj
        from updown.apps.engine import scheduler as sch
        from updown.decision.universe_names import NameChainRule

        def broken() -> dict[str, Any]:
            raise AnnouncementConfigError("깨진 설정")

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("등록 실패")

        def _kinds(_books: Any) -> tuple[str, ...]:
            return ("k_list", "k_sup", "k_title")

        def _chain(_path: Any) -> NameChainRule:
            return NameChainRule()

        monkeypatch.setattr(sel, "load_playbooks", list)
        monkeypatch.setattr(aj, "fund_announcement_kinds", _kinds)
        monkeypatch.setattr(li, "load_listing_rules", lambda: {"k_list": object()})
        monkeypatch.setattr(ss, "load_supply_rules", broken)
        rule = SimpleNamespace(kind="k_title", source="BINANCE")
        monkeypatch.setattr(an, "load_title_rules", lambda: {"k_title": rule})
        monkeypatch.setattr(un, "load_name_chain", _chain)
        monkeypatch.setattr(sch, "register_listing_job", boom)
        scheduler = sch.build_scheduler()
        sch.register_announcement_job(scheduler, cast("Any", object()), run_soon_s=None)
        assert sch.job_ids(scheduler) == ["announcements"]
