"""T832 B — 거래소 호출 수와 남은 한도를 1 분에 한 줄로 남긴다 (기록만 · 막지 않는다).

지키는 것:
- Gate 가 실제로 주는 헤더 이름(`x-gate-ratelimit-requests-remain`)을 읽는다 — 옛 이름만 찾던
  동안에는 Gate 눈금이 한 번도 안 읽혔다. 옛 이름도 계속 받는다.
- 남은 횟수는 "쓴 횟수" 로 뒤집어 계량기에 적는다. 한도 헤더만 오면 쓴 양은 건드리지 않는다.
- 한도 헤더가 없어도 **부른 사실**은 경로별로 센다.
- 주문 번호 · 종목 이름이 든 경로는 `{id}` 한 줄로 모은다.
- 1 분 요약 한 줄에 경로별 건수와 "바닥에 가장 가까웠던 길" 이 실린다.
- 한도 접근 경고는 거래소마다 10 초에 한 번만 찍는다.
- 헤더가 이상해도 던지지 않는다(규칙 #8-1).
"""
# pyright: reportUnusedFunction=false, reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from updown.marketdata import ratelimit as rl

REMAIN = "x-gate-ratelimit-requests-remain"
OLD_REMAIN = "x-gate-ratelimit-remain"
LIMIT = "x-gate-ratelimit-limit"


class _Cap:
    """로그를 모으는 가짜 — `(수준, 사건, payload)`."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.rows.append(("info", event, dict(kwargs.get("payload") or {})))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.rows.append(("warning", event, dict(kwargs.get("payload") or {})))

    def of(self, event: str) -> list[dict[str, Any]]:
        return [payload for _, name, payload in self.rows if name == event]


def _wipe() -> None:
    for box in (
        rl._PATHS,
        rl._USED_SEEN,
        rl._LAST_REPORT,
        rl._METERS,
        rl._REMAIN_SEEN,
        rl._LAST_LOUD,
    ):
        box.clear()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    _wipe()
    yield
    _wipe()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    t = [1_000_000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    return t


@pytest.fixture
def cap(monkeypatch: pytest.MonkeyPatch) -> _Cap:
    box = _Cap()
    monkeypatch.setattr(rl, "_logger", box)
    return box


def _gate(remain: int, limit: int = 200, *, path: str = "", name: str = REMAIN) -> None:
    rl.observe("GATE", {name: str(remain), LIMIT: str(limit)}, path=path)


class TestGateHeaders:
    def test_the_real_header_name_is_read(self, clock: list[float], cap: _Cap) -> None:
        del clock, cap
        _gate(150, path="/api/v4/futures/usdt/positions/BTC_USDT")
        found = rl.meter("GATE")
        assert (found.used, found.limit) == (50, 200), "남은 150 / 한도 200 = 쓴 50"

    def test_the_old_header_name_still_works(self, clock: list[float], cap: _Cap) -> None:
        del clock, cap
        _gate(10, name=OLD_REMAIN, path="/futures/usdt/orders")
        assert rl.meter("GATE").used == 190

    def test_a_limit_alone_does_not_touch_used(self, clock: list[float], cap: _Cap) -> None:
        """한도만 오면 쓴 양을 모른다 — 예전에는 한도 값을 '쓴 양' 으로 적었다."""
        del clock, cap
        _gate(150, path="/futures/usdt/orders")
        rl.observe("GATE", {LIMIT: "400"}, path="/futures/usdt/orders")
        found = rl.meter("GATE")
        assert (found.used, found.limit) == (50, 400)
        assert rl.paths_1m("GATE") == [{"path": "/futures/usdt/orders", "n": 2, "weight": 2}]

    def test_a_remain_without_a_limit_cannot_be_flipped(
        self, clock: list[float], cap: _Cap
    ) -> None:
        del clock, cap
        rl.observe("GATE", {REMAIN: "150"}, path="/futures/usdt/orders")
        found = rl.meter("GATE")
        assert (found.used, found.limit) == (0, 0), "한도를 모르면 쓴 양으로 못 뒤집는다"
        assert rl.tightest_1m("GATE") == [
            {"path": "/futures/usdt/orders", "remain_min": 150, "limit": 0}
        ]

    def test_calls_are_counted_without_any_header(self, clock: list[float], cap: _Cap) -> None:
        del clock, cap
        rl.observe("GATE", {}, path="/futures/usdt/orders")
        rl.observe("GATE", {"content-type": "json"}, path="/futures/usdt/orders")
        assert rl.paths_1m("GATE") == [{"path": "/futures/usdt/orders", "n": 2, "weight": 2}]
        assert rl.peak_used_1m("GATE") == 0, "헤더가 없었으면 소모 최댓값을 꾸며 넣지 않는다"

    def test_a_broken_header_never_raises(self, clock: list[float], cap: _Cap) -> None:
        del clock, cap
        rl.observe("GATE", {REMAIN: "abc", LIMIT: "200"}, path="/futures/usdt/orders")
        rl.observe("GATE", None, path="/futures/usdt/orders")
        rl.observe("GATE", {REMAIN: "1", LIMIT: "x"}, path="/futures/usdt/orders")


class TestShapeOf:
    def test_order_ids_and_symbols_fold_into_one_line(self) -> None:
        assert rl.shape_of("/api/v4/futures/usdt/orders/123456") == "/futures/usdt/orders/{id}"
        assert rl.shape_of("/futures/usdt/positions/BTC_USDT") == "/futures/usdt/positions/{id}"
        assert rl.shape_of("/futures/usdt/price_orders/77") == "/futures/usdt/price_orders/{id}"
        assert (
            rl.shape_of("/futures/usdt/positions/1000PEPE_USDT/leverage")
            == "/futures/usdt/positions/{id}/leverage"
        )

    def test_plain_paths_stay_as_they_are(self) -> None:
        for path in ("/fapi/v1/klines", "/fapi/v2/positionRisk", "/futures/usdt/tickers", ""):
            assert rl.shape_of(path) == path

    def test_public_and_signed_clients_land_on_the_same_line(
        self, clock: list[float], cap: _Cap
    ) -> None:
        del clock, cap
        rl.observe("GATE", {}, path="/api/v4/futures/usdt/orders/1")
        rl.observe("GATE", {}, path="/futures/usdt/orders/2")
        assert rl.paths_1m("GATE") == [{"path": "/futures/usdt/orders/{id}", "n": 2, "weight": 2}]


class TestTightest:
    def test_the_lowest_remain_per_path_closest_first(self, clock: list[float], cap: _Cap) -> None:
        del clock, cap
        _gate(190, path="/futures/usdt/orders")
        _gate(120, path="/futures/usdt/orders")
        _gate(150, path="/futures/usdt/orders")
        _gate(5, 10, path="/futures/usdt/price_orders")
        _gate(30, path="/futures/usdt/positions/BTC_USDT")
        assert rl.tightest_1m("GATE") == [
            {"path": "/futures/usdt/positions/{id}", "remain_min": 30, "limit": 200},
            {"path": "/futures/usdt/price_orders", "remain_min": 5, "limit": 10},
            {"path": "/futures/usdt/orders", "remain_min": 120, "limit": 200},
        ]

    def test_old_rows_leave_the_window(self, clock: list[float], cap: _Cap) -> None:
        del cap
        _gate(3, path="/futures/usdt/orders")
        clock[0] += rl.PATH_WINDOW_S + 1
        assert rl.tightest_1m("GATE") == []
        _gate(180, path="/futures/usdt/orders")
        assert rl.tightest_1m("GATE")[0]["remain_min"] == 180

    def test_binance_has_no_remain_rows(self, clock: list[float], cap: _Cap) -> None:
        del clock, cap
        rl.observe("BINANCE", {"x-mbx-used-weight-1m": "100"}, limit=2400, path="/fapi/v1/klines")
        assert rl.tightest_1m("BINANCE") == []


class TestMinuteLine:
    def test_one_line_a_minute_with_counts_and_the_tightest_path(
        self, clock: list[float], cap: _Cap
    ) -> None:
        _gate(199, path="/futures/usdt/accounts")
        assert len(cap.of("ratelimit_paths")) == 1, "첫 응답은 바로 한 줄"
        clock[0] += 10
        for order in ("11", "12", "13"):
            _gate(60, path=f"/futures/usdt/orders/{order}")
        _gate(195, path="/futures/usdt/positions/BTC_USDT")
        assert len(cap.of("ratelimit_paths")) == 1, "1 분 안에는 다시 안 찍는다"

        clock[0] += 51
        _gate(198, path="/futures/usdt/accounts")
        lines = cap.of("ratelimit_paths")
        assert len(lines) == 2
        line = lines[1]
        assert line["venue"] == "GATE"
        assert line["total_n"] == 5, "61 초 전 첫 호출은 창 밖"
        assert line["top"][0] == {"path": "/futures/usdt/orders/{id}", "n": 3}
        assert line["tightest"][0] == {
            "path": "/futures/usdt/orders/{id}",
            "remain_min": 60,
            "limit": 200,
        }
        assert "estimated_weight" not in line, "Gate 는 가중치 합이 뜻이 없다"

    def test_binance_keeps_its_weight_line(self, clock: list[float], cap: _Cap) -> None:
        del clock
        rl.observe("BINANCE", {"x-mbx-used-weight-1m": "100"}, limit=2400, path="/fapi/v1/klines")
        line = cap.of("ratelimit_paths")[0]
        assert line["estimated_weight"] == 2 and line["peak_used"] == 100
        assert "tightest" not in line

    def test_headerless_calls_still_get_their_line(self, clock: list[float], cap: _Cap) -> None:
        del clock
        rl.observe("GATE", {}, path="/futures/usdt/orders")
        line = cap.of("ratelimit_paths")[0]
        assert line["total_n"] == 1 and line["venue"] == "GATE"


class TestLoudOncePerTenSeconds:
    def test_a_burst_near_the_limit_warns_once(self, clock: list[float], cap: _Cap) -> None:
        for _ in range(5):
            _gate(10)
        assert len(cap.of("ratelimit_close")) == 1
        clock[0] += rl.LOUD_EVERY_S - 1
        _gate(8)
        assert len(cap.of("ratelimit_close")) == 1
        clock[0] += 2
        _gate(6)
        warns = cap.of("ratelimit_close")
        assert len(warns) == 2
        assert warns[1]["used"] == 194 and warns[1]["limit"] == 200 and warns[1]["share"] == "97%"

    def test_each_venue_has_its_own_timer(self, clock: list[float], cap: _Cap) -> None:
        del clock
        _gate(10)
        rl.observe("BINANCE", {"x-mbx-used-weight-1m": "2000"}, limit=2400)
        assert [w["venue"] for w in cap.of("ratelimit_close")] == ["GATE", "BINANCE"]

    def test_far_from_the_limit_stays_quiet(self, clock: list[float], cap: _Cap) -> None:
        del clock
        _gate(150)
        assert cap.of("ratelimit_close") == []
