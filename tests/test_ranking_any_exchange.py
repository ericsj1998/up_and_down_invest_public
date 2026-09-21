"""종목 순위는 **연결된 거래소**를 따라간다 (2026-09-22).

🔴 사용자 지적: *"항상 내가 하드코딩 하지 말라고 했지 — 바이낸스 테스트넷에서는 바이낸스로,
Gate 에서는 Gate 로 떠야지."*

그 화면의 서버 쪽은 `Market.GATE` 와 `GateAdapter` 를 직접 찾고, Gate 원문 필드를 읽고,
Gate 이름 아홉 개의 튜플을 돌았다. 바이낸스 테스트넷에 연결된 로컬 데모에서는 *"연결된
Gate 계정이 없다"* 만 떴다. 여기서 못 박는 것:

    ① 순위 코드에 거래소 이름이 없다 (능력 `TickerBoard` 로 고른다)
    ② 두 어댑터가 같은 자료형(`TickerStat`)을 낸다 — 심볼은 도메인 표기다
    ③ 종목 우주는 파생이다 (하드코딩 튜플이 없다)
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api import exchange as api
from updown.common.domain.market import TickerStat
from updown.marketdata.adapter import TickerBoard
from updown.marketdata.binance.adapter import BinanceAdapter
from updown.marketdata.gate.adapter import GateAdapter


def _code_only(func: Any) -> str:
    """함수의 **실제 코드만** — 독스트링과 주석을 뺀다.

    Note:
        주석·독스트링은 "전에는 `Market.GATE` 를 직접 찾았다" 처럼 옛 이름을 **설명**한다.
        그것까지 잡으면 경위를 적을 수 없게 된다. AST 로 되돌려 쓰면 주석은 사라지고,
        독스트링은 첫 문장을 지워서 뺀다. 문자열은 작은따옴표로 통일된다(`ast.unparse`).
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class FakeClient:
    """경로별로 정해 둔 응답을 돌려주는 조회 클라이언트."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.asked: list[str] = []

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # 전 종목 조회는 **심볼 없이** 부른다 — 심볼을 넣으면 한 종목만 온다.
        assert not params, f"전 종목 조회에 인자가 붙었다: {params}"
        self.asked.append(path)
        return self.replies[path]


class TestNoExchangeNames:
    def test_ranking_code_names_no_exchange(self) -> None:
        """🔴 상위 계층에 거래소 이름이 나타나면 추상화가 샌 것이다 (CLAUDE.md §1)."""
        for func in (api.ranking, api.symbols, api._board_market, api._universe):  # pyright: ignore[reportPrivateUsage]
            body = _code_only(func)
            for name in ("Market.GATE", "Market.BINANCE", "GateAdapter", "BinanceAdapter"):
                assert name not in body, f"{func.__name__} 가 {name} 을 직접 안다"
            # 문자열 리터럴로 박는 것도 같은 하드코딩이다.
            for literal in ("'GATE'", "'BINANCE'"):
                assert literal not in body, f"{func.__name__} 에 거래소 이름 리터럴 {literal}"

    def test_there_is_no_hardcoded_symbol_tuple(self) -> None:
        """⛔ 목록을 손으로 적으면 반드시 낡는다 — TSLAX 는 거래소에서 사라진 뒤에도 떠 있었다."""
        assert not hasattr(api, "TRACKED")

    def test_both_adapters_promise_the_same_capability(self) -> None:
        """능력으로 고르므로, 두 어댑터가 그 능력을 지켜야 순위가 어느 쪽에서든 뜬다."""
        assert isinstance(GateAdapter(FakeClient({})), TickerBoard)  # type: ignore[arg-type]
        assert isinstance(BinanceAdapter(FakeClient({})), TickerBoard)  # type: ignore[arg-type]


class TestSameShapeFromBothExchanges:
    async def test_gate_rows_become_ticker_stats(self) -> None:
        client = FakeClient(
            {
                "/futures/usdt/tickers": [
                    {
                        "contract": "BTC_USDT",
                        "last": "85000.5",
                        "high_24h": "86000",
                        "low_24h": "83000",
                        "highest_bid": "85000.4",
                        "lowest_ask": "85000.6",
                        "volume_24h_quote": "9388883052",
                        "change_percentage": "1.25",
                    }
                ]
            }
        )
        got = await GateAdapter(client).ticker_stats()  # type: ignore[arg-type]

        assert got == [
            TickerStat(
                symbol="BTC_USDT",
                last=Decimal("85000.5"),
                high_24h=Decimal("86000"),
                low_24h=Decimal("83000"),
                bid=Decimal("85000.4"),
                ask=Decimal("85000.6"),
                turnover_quote=Decimal("9388883052"),
                change_pct=Decimal("1.25"),
            )
        ]

    async def test_binance_joins_two_calls_and_restores_the_symbol(self) -> None:
        """바이낸스는 통계와 호가가 **둘로 갈려** 온다 — 그리고 심볼이 `BTCUSDT` 다."""
        client = FakeClient(
            {
                "/fapi/v1/ticker/24hr": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "85000.5",
                        "highPrice": "86000",
                        "lowPrice": "83000",
                        "quoteVolume": "9388883052",
                        "priceChangePercent": "1.25",
                    }
                ],
                "/fapi/v1/ticker/bookTicker": [
                    {"symbol": "BTCUSDT", "bidPrice": "85000.4", "askPrice": "85000.6"}
                ],
            }
        )
        got = await BinanceAdapter(client).ticker_stats()  # type: ignore[arg-type]

        assert len(got) == 1
        # 🔴 도메인 표기로 되돌아와야 한다 — 안 그러면 우주(`BTC_USDT`)와 안 맞아 전 종목이
        #    "거래소가 이 계약을 안 준다" 로 뜬다.
        assert got[0].symbol == "BTC_USDT"
        assert got[0].bid == Decimal("85000.4")
        assert got[0].turnover_quote == Decimal("9388883052")

    async def test_a_broken_cell_is_none_not_zero(self) -> None:
        """⛔ 0 으로 채우면 "거래가 없다 · 안 움직였다" 로 읽힌다."""
        client = FakeClient(
            {
                "/futures/usdt/tickers": [
                    {"contract": "ETH_USDT", "last": "", "high_24h": "abc", "low_24h": "NaN"}
                ]
            }
        )
        got = await GateAdapter(client).ticker_stats()  # type: ignore[arg-type]

        assert got[0].last is None
        assert got[0].high_24h is None
        assert got[0].low_24h is None
        assert got[0].turnover_quote is None

    async def test_binance_without_a_book_row_still_lists_the_symbol(self) -> None:
        """호가가 안 온 심볼도 줄은 남는다 — 스프레드 칸만 빈다."""
        client = FakeClient(
            {
                "/fapi/v1/ticker/24hr": [{"symbol": "ETHUSDT", "lastPrice": "2600"}],
                "/fapi/v1/ticker/bookTicker": [],
            }
        )
        got = await BinanceAdapter(client).ticker_stats()  # type: ignore[arg-type]

        assert got[0].symbol == "ETH_USDT"
        assert got[0].bid is None
        assert got[0].ask is None


class TestTradableFollowsTheMarket:
    def test_each_market_reads_its_own_tick_declarations(self) -> None:
        """🔴 전에는 어느 거래소든 Gate 의 눈금 선언으로 "띄울 수 있다" 를 답했다."""
        gate = api._tradable("GATE")  # pyright: ignore[reportPrivateUsage]
        binance = api._tradable("BINANCE")  # pyright: ignore[reportPrivateUsage]

        assert "BTC_USDT" in gate
        assert gate != binance, "두 거래소의 눈금 선언은 다른 블록이다"

    def test_a_market_without_a_cost_block_can_launch_nothing(self) -> None:
        """눈금을 모르는 시장은 빈 집합이다 — 터지지 않고, 아는 척도 안 한다."""
        assert api._tradable("NOPE") == frozenset()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("symbol", "label"),
    [("BTC_USDT", "BTC 무기한"), ("GOOGL_USDT", "GOOGL 무기한"), ("RAW", "RAW")],
)
def test_labels_come_from_the_symbol(symbol: str, label: str) -> None:
    """이름표를 따로 두면 그것도 갱신 대상이 된다 — 심볼에서 만든다."""
    assert api._label(symbol) == label  # pyright: ignore[reportPrivateUsage]
