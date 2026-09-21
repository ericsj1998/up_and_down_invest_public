"""종목 순위의 **탭과 태그** — 코인 · 주식 추종 · 지수 추종 (2026-09-22).

사용자: *"탭 하나는 코인이고, 남은 하나는 META 구글 애플 뭐 이런 거 추종하는 USDT, 그리고
지수 추종 이렇게 3개 정도는 있어야 할 것 같고, 숏을 추종하는 종목은 따로 태그 달아줘야 할 것
같아. 기존 종목 순위에 있던 것들 중 이런 것들도 구분해서 옮겨줘야해. 그리고 종목 순위 띄우는데
좀 오래 걸리긴 한다."*

여기서 못 박는 것:

    ① 묶음 표(`config/symbol_groups.yml`)가 탭 셋과 '숏 추종' 태그를 선언한다
    ② 코인 목록에 섞여 있던 주식 추종 계약(SPCX · SNDK · SKHY)이 주식 탭에 있다
    ③ 순위는 **고른 탭의 종목만** 잰다 (안 보는 탭의 호가·봉을 묻지 않는다)
    ④ 순위가 거래소에 **서명 조회를 하지 않는다** (실측: 그 한 단계가 전체의 70% 였다)
    ⑤ TTL 안의 두 번째 요청은 거래소로 안 나간다
    ⑥ 설정이 깨지면 조용히 "전부 코인" 이 되지 않는다 (규칙 #8)
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from updown.apps.api import exchange as api
from updown.common.domain.market import TickerStat
from updown.common.symbol_groups import SymbolGroupConfigError, load_symbol_groups


class FakeBoard:
    """전 종목 요약을 주는 가짜 조회 어댑터 — 몇 번 불렸는지와 어느 종목의 봉을 물었는지 센다."""

    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.symbols = symbols
        self.board_calls = 0
        self.candle_asks: list[str] = []

    async def ticker_stats(self) -> list[TickerStat]:
        self.board_calls += 1
        one = Decimal(1)
        return [
            TickerStat(
                symbol=s,
                last=one,
                high_24h=one,
                low_24h=one,
                bid=one,
                ask=one,
                turnover_quote=one,
                change_pct=Decimal(0),
            )
            for s in self.symbols
        ]

    async def get_candles(self, instrument: Any, *_: Any) -> list[Any]:
        self.candle_asks.append(instrument.symbol)
        return []


UNIVERSE = ("AAPL_USDT", "BTC_USDT", "ETH_USDT", "SKHY_USDT", "SQQQ_USDT", "SPY_USDT")


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBoard]:
    """연결된 거래소 = 가짜 GATE · 주문 어댑터 없음(조회 전용) · 캐시는 시험마다 비운다."""
    fake = FakeBoard(UNIVERSE)

    async def picked(_: str | None) -> tuple[str, Any]:
        return "GATE", fake

    def no_orders(_: str) -> Any:
        raise api.HTTPException(status_code=503, detail="조회 전용")

    def signed_call_is_forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("순위가 거래소에 서명 조회(포지션)를 했다 — 제일 느린 단계였다")

    monkeypatch.setattr(api, "_board_market", picked)

    def universe(_: str) -> tuple[str, ...]:
        return UNIVERSE

    monkeypatch.setattr(api, "_universe", universe)
    monkeypatch.setattr(api, "_orders_adapter", no_orders)
    monkeypatch.setattr(api, "_tracked", signed_call_is_forbidden)
    api._STATE_CACHE.entries.clear()  # pyright: ignore[reportPrivateUsage]
    yield fake
    api._STATE_CACHE.entries.clear()  # pyright: ignore[reportPrivateUsage]


class TestDeclaredGroups:
    def test_three_tabs_and_coin_is_the_default(self) -> None:
        table = load_symbol_groups()
        assert [g.key for g in table.groups] == ["coin", "stock", "index"]
        assert table.default == "coin"
        # 표에 없는 종목은 전부 코인이다 — 코인을 일일이 적지 않는다.
        assert table.of("GATE", "BTC_USDT").group == "coin"
        assert table.of("BINANCE", "AAPL_USDT").group == "coin"  # 그 거래소에 선언이 없다

    @pytest.mark.parametrize("symbol", ["SPCX_USDT", "SNDK_USDT", "SKHY_USDT"])
    def test_stock_tokens_left_the_coin_list(self, symbol: str) -> None:
        """기존 종목 순위의 코인 목록에 섞여 있던 셋 — 사용자: *"구분해서 옮겨줘야해"*."""
        info = load_symbol_groups().of("GATE", symbol)
        assert info.group == "stock"
        assert info.name, "티커만으로는 무엇인지 모른다 — 풀이가 있어야 한다"

    def test_inverse_products_carry_the_short_tag(self) -> None:
        table = load_symbol_groups()
        labels = {t.key: t.label for t in table.tags}
        assert labels["inverse"] == "숏 추종"
        for symbol in ("SQQQ_USDT", "TZA_USDT"):
            assert "inverse" in table.of("GATE", symbol).tags
        assert "inverse" not in table.of("GATE", "TQQQX_USDT").tags
        assert "inverse" not in table.of("GATE", "SPY_USDT").tags

    def test_the_memecoin_spx_is_not_an_index(self) -> None:
        """🔴 SPX_USDT 는 S&P500 이 아니라 밈코인 SPX6900 이다 (실측 2026-09-22)."""
        assert load_symbol_groups().of("GATE", "SPX_USDT").group == "coin"

    def test_declared_symbols_join_the_universe(self) -> None:
        """묶음에 적힌 종목은 눈금 선언·펀드·판이 없어도 순위에 뜬다."""
        found = set(api._universe("GATE"))  # pyright: ignore[reportPrivateUsage]
        assert set(load_symbol_groups().declared("GATE", "index")) <= found
        assert set(load_symbol_groups().declared("GATE", "stock")) <= found


class TestDeclaredContractsCanBeLaunched:
    """탭에 보이기만 하고 판을 못 띄우면 반쪽이다 — 눈금과 이름이 같이 있어야 한다."""

    def test_every_declared_contract_has_a_measured_tick(self) -> None:
        known = api._tradable("GATE")  # pyright: ignore[reportPrivateUsage]
        table = load_symbol_groups()
        declared = set(table.declared("GATE", "stock")) | set(table.declared("GATE", "index"))
        assert declared <= known, f"눈금 미선언: {sorted(declared - known)}"

    def test_declared_contracts_resolve_to_gate_instruments(self) -> None:
        from updown.common.domain.instrument import Market
        from updown.marketdata.ingest.universe import to_instrument

        item = to_instrument("MSFT_USDT")
        assert item.market is Market.GATE
        assert item.name == "마이크로소프트 무기한"
        assert to_instrument("SQQQ_USDT").name.startswith("나스닥100 3배 역방향")


class TestBrokenConfigIsLoud:
    def _load(self, tmp_path: Path, text: str) -> Any:
        path = tmp_path / "groups.yml"
        path.write_text(text, encoding="utf-8")
        return load_symbol_groups(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(SymbolGroupConfigError):
            load_symbol_groups(tmp_path / "none.yml")

    def test_unknown_tag(self, tmp_path: Path) -> None:
        text = (
            "default: coin\ngroups:\n  - {key: coin, label: 코인}\n  - {key: stock, label: 주식}\n"
            "markets:\n  GATE:\n    stock:\n      AAPL_USDT: {tags: [nope]}\n"
        )
        with pytest.raises(SymbolGroupConfigError, match="태그"):
            self._load(tmp_path, text)

    def test_symbols_under_the_default_group(self, tmp_path: Path) -> None:
        text = (
            "default: coin\ngroups:\n  - {key: coin, label: 코인}\n"
            "markets:\n  GATE:\n    coin:\n      BTC_USDT: {}\n"
        )
        with pytest.raises(SymbolGroupConfigError, match="기본 묶음"):
            self._load(tmp_path, text)

    def test_default_must_be_a_declared_group(self, tmp_path: Path) -> None:
        with pytest.raises(SymbolGroupConfigError):
            self._load(tmp_path, "default: coin\ngroups:\n  - {key: stock, label: 주식}\n")


class TestRankingPerTab:
    @pytest.mark.usefixtures("board")
    async def test_default_tab_is_coin_only(self) -> None:
        got = await api.ranking()
        assert got["group"] == "coin"
        assert [r["symbol"] for r in got["rows"]] == ["BTC_USDT", "ETH_USDT"]
        assert [g["key"] for g in got["groups"]] == ["coin", "stock", "index"]
        assert got["tags"]["inverse"]["label"] == "숏 추종"

    @pytest.mark.usefixtures("board")
    async def test_stock_tab(self) -> None:
        got = await api.ranking(group="stock")
        rows = {r["symbol"]: r for r in got["rows"]}
        assert set(rows) == {"AAPL_USDT", "SKHY_USDT"}
        assert rows["SKHY_USDT"]["name"] == "SK하이닉스"
        assert rows["AAPL_USDT"]["group"] == "stock"

    @pytest.mark.usefixtures("board")
    async def test_index_tab_tags_the_inverse_product(self) -> None:
        got = await api.ranking(group="index")
        rows = {r["symbol"]: r for r in got["rows"]}
        assert set(rows) == {"SPY_USDT", "SQQQ_USDT"}
        assert rows["SQQQ_USDT"]["tags"] == ["inverse", "leveraged"]
        assert rows["SPY_USDT"]["tags"] == []

    async def test_only_the_picked_tab_is_measured(self, board: FakeBoard) -> None:
        """⭐ 비용은 종목 수에 비례한다 — 안 보는 탭의 봉을 묻지 않는다."""
        await api.ranking(group="index")
        assert sorted(board.candle_asks) == ["SPY_USDT", "SQQQ_USDT"]

    async def test_an_empty_tab_asks_the_exchange_nothing(
        self, board: FakeBoard, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """이 거래소에 그 탭의 종목이 없으면 전 종목 요약도 안 부른다 (바이낸스는 가중치 40)."""

        def coins_only(_: str) -> tuple[str, ...]:
            return ("BTC_USDT", "ETH_USDT")

        monkeypatch.setattr(api, "_universe", coins_only)
        got = await api.ranking(group="stock")
        assert got["rows"] == []
        assert got["group"] == "stock"
        assert board.board_calls == 0

    @pytest.mark.usefixtures("board")
    async def test_unknown_group_falls_back_to_default(self) -> None:
        got = await api.ranking(group="nope")
        assert got["group"] == "coin"

    async def test_second_request_inside_ttl_stays_home(self, board: FakeBoard) -> None:
        first = await api.ranking(group="stock")
        second = await api.ranking(group="stock")
        assert board.board_calls == 1
        assert first["at"] == second["at"], "기억한 표는 잰 시각도 그대로 말해야 한다"
        # 다른 탭은 다른 키다 — 한 탭의 기억이 다른 탭을 가리지 않는다.
        await api.ranking(group="coin")
        assert board.board_calls == 2

    @pytest.mark.usefixtures("board")
    async def test_a_delisted_contract_stays_visible_with_its_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 조용히 빼지 않는다 (규칙 #8) — 그리고 어느 탭의 줄인지도 말한다."""

        def wider(_: str) -> tuple[str, ...]:
            return (*UNIVERSE, "TZA_USDT")

        monkeypatch.setattr(api, "_universe", wider)
        got = await api.ranking(group="index")
        gone = next(r for r in got["rows"] if r["symbol"] == "TZA_USDT")
        assert gone["missing"]
        assert gone["group"] == "index"
        assert "inverse" in gone["tags"]


class TestSymbolsPicker:
    @pytest.mark.usefixtures("board")
    async def test_rows_say_their_group(self) -> None:
        got = await api.symbols()
        rows = {r["symbol"]: r for r in got["rows"]}
        assert rows["BTC_USDT"]["group"] == "coin"
        assert rows["SQQQ_USDT"]["group"] == "index"
        assert [g["key"] for g in got["groups"]] == ["coin", "stock", "index"]
