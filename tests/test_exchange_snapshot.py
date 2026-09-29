"""T330 — 거래소 한 벌 스냅샷: 화면이 판마다 묻던 것을 시장마다 한 번으로 (2026-09-30).

Note:
    🔴 2026-09-30 17:48 KST 실계좌 CPU 급등의 원인이 화면 폴링이었다 — RUN 카드 40개 x 10초가
    요청마다 Gate `positions/<종목>` · 조건부 · 잔고를 새로 물어 10분에 거래소 호출 12,699
    (닫힌 화면 2,414 의 5.3배). 여기서 재는 것: ① 한 벌은 TTL 안에서 한 번만 나간다
    ② 종목별 경로와 **같은 칸**을 낸다 ③ 못 하는 어댑터는 예전 경로 그대로.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from updown.apps.api import exchange as api
from updown.apps.api import exchange_snapshot as snap_mod
from updown.common.domain.instrument import Currency
from updown.common.domain.market import Balance
from updown.execution import gate_paper
from updown.orchestration.walkforward.live_runner import BookSnapshotter

RAW_POSITION = {
    "contract": "ADA_USDT",
    "size": "-4843",
    "entry_price": "0.3434",
    "mark_price": "0.3400",
    "unrealised_pnl": "1.2",
    "leverage": "4",
    "margin": "41.6",
    "liq_price": "0.41",
    "value": "164.7",
    "extra_field": "ignored",
}
RAW_STOP = {
    "id": "9344",
    "create_time": "1790000000",
    "trigger": {"price": "0.3434", "expiration": 86400},
    "initial": {"contract": "ADA_USDT", "size": "4843", "reduce_only": True, "text": "t-stop"},
}
RAW_ORDER = {
    "id": "77",
    "contract": "BTC_USDT",
    "size": "1",
    "left": "1",
    "price": "60000",
    "text": "t-entry",
    "status": "open",
    "is_reduce_only": False,
    "create_time": "1790000001",
}


class _Trade:
    """Gate 서명 클라이언트 대역 — 몇 번 물었는지 센다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_positions(self) -> list[dict[str, Any]]:
        self.calls.append("positions")
        return [RAW_POSITION, {"contract": "ETH_USDT", "size": "0"}]

    async def get_position(self, contract: str) -> dict[str, Any]:
        self.calls.append(f"positions/{contract}")
        return RAW_POSITION if contract == "ADA_USDT" else {"contract": contract, "size": "0"}

    async def list_orders(self, contract: str | None, status: str = "open") -> list[dict[str, Any]]:
        self.calls.append(f"orders:{contract}:{status}")
        rows = [RAW_ORDER]
        return rows if contract in (None, "BTC_USDT") else []

    async def list_stops(self, contract: str | None) -> list[dict[str, Any]]:
        self.calls.append(f"stops:{contract}")
        return [RAW_STOP] if contract in (None, "ADA_USDT") else []

    async def get_account(self) -> dict[str, Any]:
        self.calls.append("account")
        return {"total": "800", "available": "700", "position_margin": "0", "order_margin": "0"}


class _Book:
    """`BookSnapshotter` 인 어댑터 대역 — 게이트 어댑터의 정규화 함수를 그대로 쓴다."""

    def __init__(self) -> None:
        self.trade = _Trade()
        self.asked: list[str] = []

    async def get_balance(self) -> Balance:
        self.trade.calls.append("balance")
        return Balance(
            "gate", Currency.USD, Decimal("700"), Decimal("41.6"), None, datetime.now(UTC)
        )

    async def book_snapshot(self) -> dict[str, dict[str, Any]]:
        positions = {
            str(r["contract"]): gate_paper.position_row(r)
            for r in await self.trade.get_positions()
            if r.get("size") not in ("0", 0)
        }
        orders: dict[str, list[dict[str, str]]] = {}
        for r in await self.trade.list_orders(None, "open"):
            orders.setdefault(str(r["contract"]), []).append(gate_paper.order_row(r))
        stops: dict[str, list[dict[str, str]]] = {}
        for r in await self.trade.list_stops(None):
            stops.setdefault(str(r["initial"]["contract"]), []).append(gate_paper.stop_row(r))
        return {"positions": positions, "orders": orders, "stops": stops}

    # 종목별 경로(예전 화면 경로) — 스냅샷이 있으면 **불리지 않아야** 한다.
    async def position_snapshot(self, instrument: object) -> dict[str, str]:
        self.asked.append(f"pos:{getattr(instrument, 'symbol', '')}")
        raw = await self.trade.get_position(str(getattr(instrument, "symbol", "")))
        return {} if raw.get("size") in ("0", 0) else gate_paper.position_row(raw)

    async def open_orders(self, instrument: object) -> list[dict[str, str]]:
        self.asked.append(f"orders:{getattr(instrument, 'symbol', '')}")
        rows = await self.trade.list_orders(str(getattr(instrument, "symbol", "")), "open")
        return [gate_paper.order_row(r) for r in rows]

    async def open_stops(self, instrument: object) -> list[dict[str, str]]:
        self.asked.append(f"stops:{getattr(instrument, 'symbol', '')}")
        rows = await self.trade.list_stops(str(getattr(instrument, "symbol", "")))
        return [gate_paper.stop_row(r) for r in rows]


class _PerSymbolOnly:
    """종목별 메서드만 있는 어댑터 — `book_snapshot` 이 없어 프로토콜에서 빠진다(예전 경로)."""

    def __init__(self) -> None:
        self._inner = _Book()

    @property
    def asked(self) -> list[str]:
        return self._inner.asked

    async def position_snapshot(self, instrument: object) -> dict[str, str]:
        return await self._inner.position_snapshot(instrument)

    async def open_orders(self, instrument: object) -> list[dict[str, str]]:
        return await self._inner.open_orders(instrument)

    async def open_stops(self, instrument: object) -> list[dict[str, str]]:
        return await self._inner.open_stops(instrument)


class _Plain:
    """한 벌을 못 주는 어댑터 — `BookSnapshotter` 가 아니다."""

    async def get_balance(self) -> Balance:
        return Balance("x", Currency.USD, Decimal(1), Decimal(0), None, datetime.now(UTC))


SYMBOLS = ("BTC_USDT", "ETH_USDT", "ADA_USDT", "SOL_USDT")


class TestVenueSnapshot:
    def setup_method(self) -> None:
        snap_mod.forget()

    async def test_one_fetch_serves_many_reads_inside_ttl(self) -> None:
        """🔴 40판이 10초 안에 물어도 거래소는 **한 벌(잔고 1 + 셋)** 만 나간다."""
        book = _Book()
        first = await snap_mod.venue_snapshot("GATE", book)
        for _ in range(39):
            again = await snap_mod.venue_snapshot("GATE", book)
            assert again is first
        assert book.trade.calls == ["balance", "positions", "orders:None:open", "stops:None"]

    async def test_snapshot_rows_match_per_symbol_shape(self) -> None:
        """🔴 한 벌의 칸이 종목별 메서드와 같아야 화면이 한 글자도 안 바뀐다."""
        book = _Book()
        got = await snap_mod.venue_snapshot("GATE", book)
        assert got is not None
        assert got.position_of("ADA_USDT") == await book.position_snapshot(_Instrument("ADA_USDT"))
        assert got.position_of("ETH_USDT") == {}  # size 0 껍데기는 없다
        assert got.stops["ADA_USDT"] == await book.open_stops(_Instrument("ADA_USDT"))
        assert got.orders["BTC_USDT"] == await book.open_orders(_Instrument("BTC_USDT"))
        assert "extra_field" not in got.position_of("ADA_USDT")

    async def test_adapter_without_book_returns_none(self) -> None:
        """⚠️ 못 하는 어댑터(바이낸스 · 주식 페이퍼)는 None — 부르는 쪽이 예전 경로로 간다."""
        assert not isinstance(_Plain(), BookSnapshotter)
        assert await snap_mod.venue_snapshot("BINANCE", _Plain()) is None

    async def test_markets_do_not_share_a_snapshot(self) -> None:
        a, b = _Book(), _Book()
        one = await snap_mod.venue_snapshot("GATE", a)
        two = await snap_mod.venue_snapshot("BINANCE", b)
        assert one is not two
        assert b.trade.calls[:1] == ["balance"]

    async def test_forget_makes_the_next_read_fetch_again(self) -> None:
        book = _Book()
        await snap_mod.venue_snapshot("GATE", book)
        snap_mod.forget("GATE")
        await snap_mod.venue_snapshot("GATE", book)
        assert book.trade.calls.count("positions") == 2


class TestAllLiveFromSnapshot:
    def setup_method(self) -> None:
        snap_mod.forget()

    async def test_console_rows_come_from_the_snapshot_without_per_symbol_calls(self) -> None:
        """🔴 콘솔 상태가 종목 41개 x 3 을 묻던 자리 — 한 벌이면 종목별 호출 0."""
        book = _Book()
        got = await api._all_live(book, "GATE", SYMBOLS)  # pyright: ignore[reportPrivateUsage]
        assert book.asked == []
        assert [row["symbol"] for row in got["positions"]] == ["ADA_USDT"]
        assert got["positions"][0]["size"] == "-4843"
        assert [row["symbol"] for row in got["stops"]] == ["ADA_USDT"]
        assert got["stops"][0]["trigger_price"] == "0.3434"
        assert [row["symbol"] for row in got["orders"]] == ["BTC_USDT"]

    async def test_untracked_symbols_are_left_out(self) -> None:
        """⚠️ 추적하지 않는 종목의 줄은 안 낸다 — 예전 경로와 같은 범위."""
        book = _Book()
        got = await api._all_live(book, "GATE", ("SOL_USDT",))  # pyright: ignore[reportPrivateUsage]
        assert got == {"orders": [], "stops": [], "positions": []}

    async def test_same_rows_as_the_per_symbol_path(self) -> None:
        """🔴 한 벌 경로와 종목별 경로가 **같은 줄**을 내야 한다 — 다르면 화면이 두 얼굴이 된다."""
        book = _Book()
        via_snapshot = await api._all_live(book, "GATE", SYMBOLS)  # pyright: ignore[reportPrivateUsage]
        snap_mod.forget()
        plain = _PerSymbolOnly()
        assert not isinstance(plain, BookSnapshotter)
        via_symbols = await api._all_live(plain, "GATE", SYMBOLS)  # pyright: ignore[reportPrivateUsage]
        assert via_snapshot == via_symbols
        assert len(plain.asked) == 3 * len(SYMBOLS)


class _Instrument:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class TestGateRows:
    def test_stop_row_narrows_nested_dicts(self) -> None:
        row = gate_paper.stop_row(RAW_STOP)
        assert row == {
            "id": "9344",
            "trigger_price": "0.3434",
            "expiration": "86400",
            "size": "4843",
            "reduce_only": "True",
            "text": "t-stop",
            "create_time": "1790000000",
        }

    def test_position_row_keeps_only_screen_fields(self) -> None:
        row = gate_paper.position_row(RAW_POSITION)
        assert tuple(row) == gate_paper.POSITION_KEEP
        assert row["size"] == "-4843"

    async def test_book_snapshot_groups_by_contract_and_drops_empty_positions(self) -> None:
        adapter = gate_paper.GatePaperAdapter.__new__(gate_paper.GatePaperAdapter)
        adapter._trade = _Trade()  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
        got = await gate_paper.GatePaperAdapter.book_snapshot(adapter)
        assert list(got["positions"]) == ["ADA_USDT"]
        assert got["stops"] == {"ADA_USDT": [gate_paper.stop_row(RAW_STOP)]}
        assert got["orders"] == {"BTC_USDT": [gate_paper.order_row(RAW_ORDER)]}
        assert adapter._trade.calls == ["positions", "orders:None:open", "stops:None"]  # type: ignore[attr-defined]

    async def test_recent_orders_all_caps_per_symbol(self) -> None:
        trade = _Trade()

        async def many(_contract: str | None, status: str = "open") -> list[dict[str, Any]]:
            assert status == "finished"
            return [dict(RAW_ORDER, id=str(i)) for i in range(60)]

        trade.list_orders = many  # type: ignore[method-assign]
        adapter = gate_paper.GatePaperAdapter.__new__(gate_paper.GatePaperAdapter)
        adapter._trade = trade  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
        got = await gate_paper.GatePaperAdapter.recent_orders_all(adapter)
        assert len(got["BTC_USDT"]) == gate_paper.RECENT_LIMIT
        assert tuple(got["BTC_USDT"][0]) == gate_paper.FINISHED_KEEP
