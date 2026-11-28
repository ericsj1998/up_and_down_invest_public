"""T1052 — 상장 시각 사건 장치(플랫폼): 사건 셈 · 원천 첫 봉 · 작업(읽기 · 짝짓기 · 실패) · 모름.

값은 장치를 재는 중립 값이다(실계좌 갈래 · 오프셋 · 문턱은 비공개 시험이 본다).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from updown.analysis.announcements import AnnouncementConfigError
from updown.analysis.listings import (
    ListingRule,
    listing_candidate,
    listing_due,
    load_listing_rules,
)
from updown.decision.universe_names import NameChainRule
from updown.orchestration.rebalancer.announce_job import (
    EventRow,
    FeedState,
    feeds_known,
    make_event_mapper,
)
from updown.orchestration.rebalancer.listing_job import (
    ListingJobConfig,
    load_listing_job_config,
    refresh_due,
    run_listing_events,
)

H = timedelta(hours=1)
NOW = datetime(2026, 11, 28, 3, 17, 30, tzinfo=UTC)
RULE = ListingRule(
    kind="k",
    source="SRC",
    offset_days=10,
    min_first_ratio=Decimal("0.5"),
    ref_symbol="REFUSDT",
    quote="USDT",
    exclude_prefix=("XX",),
    listed_since=datetime(2020, 1, 1, tzinfo=UTC),
)
CFG = ListingJobConfig(recent_days=30, pair_within_h=4, refresh_h=24, stale_h=48, pace_s=0)


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


# ── 사건 셈 ─────────────────────────────────────────────────────────────────────────────
class TestRule:
    def test_due_is_first_bar_plus_offset_and_ratio_gate(self) -> None:
        first = datetime(2026, 1, 1, 8, tzinfo=UTC)
        due, why = listing_due(first, Decimal(50), Decimal(100), RULE)
        assert (due, why) == (first + timedelta(days=10), "listed")  # 같으면 지남
        assert listing_due(first, Decimal("49.99"), Decimal(100), RULE) == (None, "small_first_bar")
        assert listing_due(first, Decimal(0), Decimal(100), RULE) == (None, "no_volume")
        assert listing_due(first, Decimal(50), None, RULE) == (None, "no_ref")
        old = datetime(2019, 12, 31, 23, tzinfo=UTC)
        assert listing_due(old, Decimal(50), Decimal(1), RULE) == (None, "before_since")
        with pytest.raises(ValueError):
            listing_due(datetime(2026, 1, 1), Decimal(1), Decimal(1), RULE)

    def test_candidate_names(self) -> None:
        assert listing_candidate("AAAUSDT", RULE)
        assert not listing_candidate("XXAUSDT", RULE)  # 빼는 접두
        assert not listing_candidate("AAABUSD", RULE)  # 결제 통화
        assert not listing_candidate("USDT", RULE)
        assert not listing_candidate("가나USDT", RULE)  # ASCII 아님

    def test_config_loader(self, tmp_path: Path) -> None:
        ok = tmp_path / "a.yml"
        ok.write_text(
            "listings:\n  k:\n    source: S\n    offset_days: 5\n    min_first_ratio: '0.1'\n"
            "    ref_symbol: R\n    listed_since: '2020-02-01'\n"
            "listing_job:\n  refresh_h: 12\n  stale_h: 36\n",
            encoding="utf-8",
        )
        got = load_listing_rules(ok)["k"]
        assert got.offset == timedelta(days=5) and got.min_first_ratio == Decimal("0.1")
        assert got.listed_since == datetime(2020, 2, 1, tzinfo=UTC)
        cfg = load_listing_job_config(ok)
        assert (cfg.refresh_h, cfg.stale_h) == (12, 36)
        bad = tmp_path / "b.yml"
        bad.write_text("listings:\n  k:\n    source: S\n    what: 1\n", encoding="utf-8")
        with pytest.raises(AnnouncementConfigError, match="모르는 칸"):
            load_listing_rules(bad)
        short = tmp_path / "c.yml"
        short.write_text("listings:\n  k:\n    source: S\n", encoding="utf-8")
        with pytest.raises(AnnouncementConfigError, match="빠졌다"):
            load_listing_rules(short)
        empty = tmp_path / "d.yml"
        empty.write_text("kinds: {}\n", encoding="utf-8")
        assert load_listing_rules(empty) == {}
        with pytest.raises(AnnouncementConfigError):
            load_listing_rules(tmp_path / "none.yml")

    def test_job_config_guards(self) -> None:
        with pytest.raises(AnnouncementConfigError, match="늘 모름"):
            ListingJobConfig(refresh_h=24, stale_h=12)
        with pytest.raises(AnnouncementConfigError):
            ListingJobConfig(check_minute=60)


# ── 원천 첫 봉 ──────────────────────────────────────────────────────────────────────────
class TestSourceFirstBar:
    def _src(self, rows: list[list[Any]], seen: list[dict[str, str]]) -> Any:
        from updown.marketdata.binance.universe_source import BinanceUniverseSource

        class _Client:
            async def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
                seen.append({"path": path, **(params or {})})
                return rows

        return BinanceUniverseSource(cast("Any", _Client()), pace_s=0.0)

    def test_first_hour_asks_from_zero_with_one_row(self) -> None:
        seen: list[dict[str, str]] = []
        at = datetime(2026, 1, 1, 8, tzinfo=UTC)
        src = self._src([[_ms(at), "1", "2", "0.5", "1.5", "10", _ms(at + H) - 1, "15"]], seen)
        got = asyncio.run(src.first_hour("AAAUSDT"))
        assert got == (at, Decimal("1.5"), Decimal(10))
        assert seen == [
            {
                "path": "/fapi/v1/klines",
                "symbol": "AAAUSDT",
                "interval": "1h",
                "startTime": "0",
                "limit": "1",
            }
        ]

    def test_hour_at_needs_the_exact_bar(self) -> None:
        at = datetime(2026, 1, 1, 8, tzinfo=UTC)
        later = [[_ms(at + H), "1", "2", "0.5", "1.5", "10", 0, "15"]]
        assert asyncio.run(self._src(later, []).hour_at("REFUSDT", at)) is None
        exact = [[_ms(at), "1", "2", "0.5", "3", "4", 0, "12"]]
        assert asyncio.run(self._src(exact, []).hour_at("REFUSDT", at)) == (Decimal(3), Decimal(4))
        assert asyncio.run(self._src([], []).first_hour("AAAUSDT")) is None  # 봉 없음(상장 전)


# ── 작업 ────────────────────────────────────────────────────────────────────────────────
class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], EventRow] = {}
        self.feeds: dict[str, FeedState] = {}
        self.marks: list[dict[str, Any]] = []

    async def feed(self, kind: str) -> FeedState | None:
        return self.feeds.get(kind)

    async def tokens(self, kind: str) -> set[str]:
        return {t for k, t in self.rows if k == kind}

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


@dataclass(frozen=True)
class Sym:
    symbol: str
    onboard_ms: int | None
    quote_asset: str = "USDT"
    is_coin_perpetual: bool = True


class FakeSource:
    def __init__(
        self,
        symbols: list[Sym],
        firsts: dict[str, tuple[datetime, Decimal, Decimal] | None],
        refs: dict[datetime, tuple[Decimal, Decimal]],
        fail: bool = False,
    ) -> None:
        self.symbols = symbols
        self.firsts = firsts
        self.refs = refs
        self.fail = fail
        self.calls = 0
        self.asked: list[str] = []

    async def perpetual_symbols(self) -> Sequence[Sym]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("막힘")
        return self.symbols

    async def first_hour(self, symbol: str) -> tuple[datetime, Decimal, Decimal] | None:
        self.calls += 1
        self.asked.append(f"first:{symbol}")
        return self.firsts.get(symbol)

    async def hour_at(self, symbol: str, at: datetime) -> tuple[Decimal, Decimal] | None:
        self.calls += 1
        self.asked.append(f"ref:{symbol}@{at:%m-%d %H}")
        return self.refs.get(at)


# 첫 봉 시각: 사건(첫 봉 + 10 일)이 NOW 보다 뒤가 되도록
FIRST = datetime(2026, 11, 18, 5, tzinfo=UTC)  # 사건 11-28 05:00 = NOW + 약 1.7 시간
FIRST_B = datetime(2026, 11, 18, 20, tzinfo=UTC)  # 사건 11-28 20:00 = NOW + 약 17 시간


def _world() -> FakeSource:
    recent = _ms(NOW - timedelta(days=11))
    return FakeSource(
        symbols=[
            Sym("OLDUSDT", _ms(NOW - timedelta(days=400))),  # 오래된 상장 — 첫 봉을 안 묻는다
            Sym("AAAUSDT", recent),  # 사건(문 지남)
            Sym("BBBUSDT", recent),  # 첫 봉이 작다
            Sym("CCCUSDT", recent),  # 봉 없음(상장 전) — 줄을 안 적는다
            Sym("DDDUSDT", recent),  # 사건 시각이 이미 지남 — 기준 봉을 안 묻는다
            Sym("EEEUSDT", recent),  # 사건(같은 첫 봉 시각 — 기준 봉은 한 번)
            Sym("FFFBUSD", recent, quote_asset="BUSD"),  # 결제 통화 다름
            Sym("GGGUSDT", recent, is_coin_perpetual=False),  # 코인 무기한 아님
            Sym("XXHUSDT", recent),  # 빼는 접두
        ],
        firsts={
            "AAAUSDT": (FIRST, Decimal(2), Decimal(30)),
            "BBBUSDT": (FIRST_B, Decimal(1), Decimal(1)),
            "CCCUSDT": None,
            "DDDUSDT": (NOW - timedelta(days=11), Decimal(5), Decimal(5)),
            "EEEUSDT": (FIRST, Decimal(5), Decimal(10)),  # 50 = 0.5 x 100 — 같으면 지남
        },
        refs={FIRST: (Decimal(10), Decimal(10)), FIRST_B: (Decimal(10), Decimal(10))},
    )


class TestJob:
    def test_first_refresh_writes_rows_and_marks_the_feed(self) -> None:
        store, src = FakeStore(), _world()
        (out,) = asyncio.run(run_listing_events(store, src, None, [RULE], now=NOW, cfg=CFG))
        assert out.refreshed and out.new_rows == 5 and not out.refresh_error
        got = {t: r.reason for (_k, t), r in store.rows.items()}
        assert got == {
            "OLDUSDT": "history",
            "AAAUSDT": "listed",
            "BBBUSDT": "small_first_bar",
            "DDDUSDT": "history",
            "EEEUSDT": "listed",
        }
        assert store.rows[("k", "AAAUSDT")].announced_at == FIRST + timedelta(days=10)
        assert store.rows[("k", "AAAUSDT")].contract is None  # 짝은 사건 앞에서
        # 오래된 상장은 첫 봉을 안 묻고 · 지난 사건은 기준 봉을 안 묻고 · 같은 시각 기준 봉은 한 번
        assert "first:OLDUSDT" not in src.asked
        assert [a for a in src.asked if a.startswith("ref:")] == [
            "ref:REFUSDT@11-18 05",
            "ref:REFUSDT@11-18 20",
        ]
        feed = store.feeds["k"]
        assert feed.backfilled_at == NOW and feed.ok_at == NOW and feed.known(NOW, 3600)
        assert out.stats["candidates"] == 6 and out.stats["pending"] == 1
        # 다음 점검(새로 읽기 간격 안)은 원천을 안 부른다 · 상장 전이던 것은 다음 읽기에 다시 묻는다
        calls = src.calls
        asyncio.run(run_listing_events(store, src, None, [RULE], now=NOW + H, cfg=CFG))
        assert src.calls == calls
        asyncio.run(run_listing_events(store, src, None, [RULE], now=NOW + 25 * H, cfg=CFG))
        assert src.asked.count("first:CCCUSDT") == 2 and src.asked.count("first:AAAUSDT") == 1

    def test_pairing_only_inside_the_window_and_once(self) -> None:
        store, src = FakeStore(), _world()
        asked: list[Sequence[str]] = []

        async def mapper(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            asked.append(list(tokens))
            return {t: (t, t.removesuffix("USDT") + "_USDT", "ok") for t in tokens}

        (out,) = asyncio.run(run_listing_events(store, src, mapper, [RULE], now=NOW, cfg=CFG))
        # 사건 11-28 05:00(약 1.7 시간 뒤)만 4 시간 창 안 — AAA · EEE
        assert out.paired == 2 and asked == [["AAAUSDT", "EEEUSDT"]]
        row = store.rows[("k", "AAAUSDT")]
        assert row.contract == "AAA_USDT" and row.reason == "ok" and row.seen_at == NOW
        # 다시 점검해도 이미 지은 짝은 다시 안 짓는다(사건 하나에 한 번)
        asyncio.run(run_listing_events(store, src, mapper, [RULE], now=NOW + H, cfg=CFG))
        assert asked == [["AAAUSDT", "EEEUSDT"]]

    def test_refresh_failure_is_recorded_and_pairing_still_runs(self) -> None:
        store = FakeStore()
        store.feeds["k"] = FeedState("k", backfilled_at=NOW - 30 * H, ok_at=NOW - 30 * H)
        store.rows[("k", "AAAUSDT")] = EventRow(
            "k", "AAAUSDT", NOW + 2 * H, NOW - 30 * H, "", "t", None, None, "listed"
        )
        src = FakeSource([], {}, {}, fail=True)

        async def mapper(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            return {t: (t, "AAA_USDT", "ok") for t in tokens}

        (out,) = asyncio.run(run_listing_events(store, src, mapper, [RULE], now=NOW, cfg=CFG))
        assert not out.refreshed and "막힘" in out.refresh_error and out.paired == 1
        feed = store.feeds["k"]
        assert feed.ok_at == NOW - 30 * H and feed.error_at == NOW  # 마지막 성공은 그대로
        # 받기 허용 나이 48 시간 — 아직 앎 · 49 시간이면 모름(그 다리 새 진입 보류)
        assert feed.known(NOW, CFG.stale_h * 3600)
        assert not feed.known(NOW + 19 * H, CFG.stale_h * 3600)

    def test_pairing_failure_keeps_the_row_waiting(self) -> None:
        store = FakeStore()
        store.feeds["k"] = FeedState("k", backfilled_at=NOW, ok_at=NOW)
        store.rows[("k", "AAAUSDT")] = EventRow(
            "k", "AAAUSDT", NOW + 2 * H, NOW, "", "t", None, None, "listed"
        )

        async def broken(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
            raise RuntimeError(f"시세 못 받음 {len(tokens)}")

        (out,) = asyncio.run(run_listing_events(store, None, broken, [RULE], now=NOW, cfg=CFG))
        assert "시세 못 받음" in out.pair_error and out.paired == 0
        assert store.rows[("k", "AAAUSDT")].reason == "listed"  # 다음 점검에 다시
        assert store.feeds["k"].ok_at == NOW and store.marks[-1]["ok"] is False

    def test_refresh_due(self) -> None:
        assert refresh_due(None, NOW, CFG)
        assert refresh_due(FeedState("k", ok_at=NOW), NOW, CFG)  # 처음 읽기 전
        fresh = FeedState("k", backfilled_at=NOW, ok_at=NOW)
        assert not refresh_due(fresh, NOW + 23 * H, CFG)
        assert refresh_due(fresh, NOW + 24 * H, CFG)


class TestOpenBarAndConfig:
    def test_open_first_bar_waits_until_it_closes(self) -> None:
        """상장 첫 시간(첫 봉이 아직 열림)엔 줄을 안 적는다 — 종가 · 거래량 · 기준 봉이 덜 찼다."""
        store = FakeStore()
        opening = NOW.replace(minute=0, second=0)  # 03:00 봉 — 03:17 에는 아직 열려 있다
        src = FakeSource(
            symbols=[Sym("NEWUSDT", _ms(opening))],
            firsts={"NEWUSDT": (opening, Decimal(2), Decimal(30))},
            refs={opening: (Decimal(10), Decimal(10))},
        )
        (out,) = asyncio.run(run_listing_events(store, src, None, [RULE], now=NOW, cfg=CFG))
        assert out.stats["pending"] == 1 and ("k", "NEWUSDT") not in store.rows
        assert not any(a.startswith("ref:") for a in src.asked)  # 기준 봉도 안 물었다
        later = NOW + 25 * H  # 다음 목록 읽기 — 이제 닫힌 봉
        asyncio.run(run_listing_events(store, src, None, [RULE], now=later, cfg=CFG))
        assert store.rows[("k", "NEWUSDT")].reason == "listed"

    def test_job_config_rejects_unknown_keys(self, tmp_path: Path) -> None:
        typo = tmp_path / "a.yml"
        typo.write_text("listing_job:\n  stale_hours: 48\n", encoding="utf-8")
        with pytest.raises(AnnouncementConfigError, match="모르는 칸"):
            load_listing_job_config(typo)


class TestKnownAndMapper:
    def test_fund_kinds_skip_declarations_outside_bundles(self) -> None:
        from types import SimpleNamespace

        from updown.orchestration.rebalancer.announce_job import fund_announcement_kinds

        def book(
            pid: str, kind: str | None, *, bundle: tuple[str, ...] = (), listed: bool = False
        ) -> Any:
            entry = None if kind is None else SimpleNamespace(kind=kind)
            return SimpleNamespace(
                playbook_id=pid, bundle=bundle, listed=listed, entry_announcement=entry
            )

        books = [
            book("w", None, bundle=("a", "b"), listed=True),
            book("a", "ka"),  # 묶음 구성원
            book("b", "kb"),
            book("c", "kc"),  # 선언만 — 묶음 밖 · 목록 밖
            book("d", "kd", listed=True),  # 홀로 목록에 오른 것
        ]
        assert fund_announcement_kinds(books) == ("ka", "kb", "kd")

    def test_feeds_known_uses_the_kind_limit(self) -> None:
        state = {
            "a": FeedState("a", backfilled_at=NOW, ok_at=NOW - timedelta(minutes=20)),
            "b": FeedState("b", backfilled_at=NOW, ok_at=NOW - 20 * H),
        }
        assert not feeds_known(state, ["a"], NOW, 900)  # 15 분 넘음
        assert not feeds_known(state, ["b"], NOW, 900)
        assert feeds_known(state, ["b"], NOW, 900, {"b": 48 * 3600})  # 갈래 값
        assert not feeds_known(state, ["a", "b"], NOW, 900, {"b": 48 * 3600})  # 하나라도 모르면
        assert not feeds_known(state, ["c"], NOW, 900, {"c": 48 * 3600})  # 표에 없음 = 모름

    def test_exact_mapper_uses_the_rank_name_as_given(self) -> None:
        class _Rank:
            async def last_prices(self) -> dict[str, float]:
                return {"1000BBBUSDT": 2.0, "1000CCCUSDT": 1.0}

        class _Order:
            async def contracts(self) -> dict[str, tuple[bool, bool]]:
                return {"BBB_USDT": (True, False), "CCC_USDT": (True, False)}

            async def last_prices(self) -> dict[str, float]:
                return {"BBB_USDT": 0.002, "CCC_USDT": 0.001}

        mapper = make_event_mapper(_Rank(), _Order(), NameChainRule(), exact=True)
        got = asyncio.run(cast("Any", mapper(["1000BBBUSDT", "CCCUSDT"])))
        assert got["1000BBBUSDT"] == ("1000BBBUSDT", "BBB_USDT", "ok")
        # 정확한 이름만 — `CCCUSDT` 가 없다고 `1000CCCUSDT` 로 바꾸지 않는다
        assert got["CCCUSDT"] == (None, None, "no_rank")
