"""콘솔이 **거래소에 열린 포지션**을 읽는 길 (2026-08-30 수정).

## 무엇이 고장나 있었나

콘솔의 `_tracked` 는 `orders.get_positions()` 를 불렀다. 그런데 그것은 어댑터가 아니라
**어댑터 안의 TradeClient** 메서드다. 어댑터에는 없으니 매 호출이 `AttributeError` 였고,
그 예외를 감싼 `except` 가 warning 으로 삼켜 버렸다:

    console_tracked_positions_unreadable  {'error': "'GatePaperAdapter' object has
                                           no attribute 'get_positions'"}

⇒ *"거래소에 열려 있는 포지션"* 축이 **조용히 죽어 있었다.** 판이 지워져도 포지션은
남고 **그 고아를 찾는 것이 콘솔의 존재 이유**인데(2026-08-25 GT ADA 116계약), 목록은
살아 있는 판의 종목만 훑고 있었다.

## 처방

어댑터에 `open_positions()` 를 두고, 콘솔은 좁은 프로토콜 `PositionLister` 로 묻는다.

⚠️ 못 하는 어댑터(업비트 등)는 **경고 없이** 넘어간다 — 포지션이 없는 시장에서
   "포지션을 못 읽는다" 는 경고는 소음이고, 소음은 진짜 경고를 가린다.
"""

from pathlib import Path
from typing import Any

import pytest

from updown.orchestration.walkforward.live_runner import PositionAware, PositionLister


class FakeTrade:
    """`get_positions` 만 있는 가짜 TradeClient."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def get_positions(self) -> list[dict[str, Any]]:
        return self.rows


class Lister:
    """어댑터가 하는 일만 흉내 낸다 — 실제 어댑터는 testnet 키가 있어야 만들어진다."""

    def __init__(self, trade: FakeTrade, key: str) -> None:
        self._trade, self._key = trade, key

    async def open_positions(self) -> list[dict[str, str]]:
        from updown.execution.gate_paper import GatePaperAdapter

        return await GatePaperAdapter.open_positions(self)  # type: ignore[arg-type]


class TestTheAdapterListsWhatIsOpen:
    @pytest.mark.anyio
    async def test_it_drops_the_husks(self) -> None:
        """⚠️ Gate 는 포지션을 닫아도 **행을 안 지운다** — `size` 0 껍데기가 남는다.

        그것을 보유로 세면 콘솔이 없는 포지션을 훑는다 (옛 TRACKED 하드 목록이
        testnet 에 없는 종목으로 CONTRACT_NOT_FOUND 를 상시 뿜던 것과 같은 증상).
        """
        rows = [
            {"contract": "BTC_USDT", "size": "3", "margin": "100"},
            {"contract": "ETH_USDT", "size": "0", "margin": "0"},
            {"contract": "ADA_USDT", "size": "0.0"},
        ]
        found = await Lister(FakeTrade(rows), "contract").open_positions()
        assert [row["symbol"] for row in found] == ["BTC_USDT"]

    @pytest.mark.anyio
    async def test_it_fills_symbol_from_either_key(self) -> None:
        """Gate 는 `contract`, 바이낸스는 `symbol` 로 부른다 — 부르는 쪽이 안 갈리게."""
        rows = [{"symbol": "BTCUSDT", "size": "1"}]
        found = await Lister(FakeTrade(rows), "symbol").open_positions()
        assert found[0]["symbol"] == "BTCUSDT"

    @pytest.mark.anyio
    async def test_everything_is_a_string(self) -> None:
        """표시용 값이다 — `Decimal` 로 바꾸면 SSoT 가 둘이 된다 (절대 규칙 #4)."""
        rows = [{"contract": "BTC_USDT", "size": 3, "margin": 100.5}]
        found = await Lister(FakeTrade(rows), "contract").open_positions()
        assert all(isinstance(v, str) for v in found[0].values())


class TestTheProtocolsStaySeparate:
    def test_a_lister_is_not_automatically_position_aware(self) -> None:
        """🔴 둘을 한 프로토콜로 합치면 `position_snapshot` 만 가진 어댑터가
        `isinstance` 에서 탈락해 **이어받기가 조용히 죽는다**."""

        class OnlyLists:
            async def open_positions(self) -> list[dict[str, str]]:
                return []

        assert isinstance(OnlyLists(), PositionLister)
        assert not isinstance(OnlyLists(), PositionAware)

    def test_both_paper_adapters_can_list(self) -> None:
        """Gate 와 바이낸스가 **같은 약속**을 지킨다 — 콘솔이 거래소를 안 가린다."""
        from updown.execution.binance_paper import BinancePaperAdapter
        from updown.execution.gate_paper import GatePaperAdapter

        for kind in (GatePaperAdapter, BinancePaperAdapter):
            assert hasattr(kind, "open_positions"), f"{kind.__name__} 이 목록을 못 낸다"


class TestTheConsoleAsksThroughTheProtocol:
    def test_it_no_longer_calls_the_trade_client_method(self) -> None:
        """🔴 `orders.get_positions()` 는 **어댑터에 없는 이름**이다 — 되살아나면 또

        AttributeError 가 warning 으로 삼켜지고 축이 다시 죽는다.
        """
        import updown.apps.api.exchange as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        # 주석은 뺀다 — 왜 고쳤는지를 적은 문장까지 금지하면 기록을 못 남긴다.
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert "orders.get_positions()" not in code
        assert "isinstance(orders, PositionLister)" in code


class TestTheAuditComparesTheSum:
    """🔴 판이 여럿이면 감사가 **손을 놓고 있었다** (2026-08-30).

    `wallet_unattributable` 이 상시 떠서 원장-사실 대조가 한 번도 안 돌았다.
    가를 수 없는 것은 *판 하나의 몫*이지 *합계*가 아니다 — 판마다 예산이 따로이므로
    `Σ 원장 == 계정 총액` 이어야 한다.
    """

    def test_the_api_wires_the_sum_wherever_it_wires_peers(self) -> None:
        import updown.apps.api.walkforward as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert source.count("runner.peers = ") == source.count("runner.account_modelled = "), (
            "peers 만 꽂고 합계를 안 꽂으면 감사가 예전처럼 물러선다"
        )

    def test_the_runner_defaults_to_not_knowing(self) -> None:
        """기본값은 `None` — 아무도 안 꽂았으면 **모른다고 말한다**.

        0 으로 기본값을 두면 "계정이 비었다" 로 읽혀 매 걸음 `wallet_drift` 가 뜬다.
        """
        import inspect

        from updown.orchestration.walkforward import live_runner

        source = inspect.getsource(live_runner.LiveRunner.__init__)
        assert "self.account_modelled: Callable[[], Decimal | None] = lambda: None" in source

    def test_the_audit_falls_back_when_the_sum_is_unknown(self) -> None:
        """합을 못 내면 예전처럼 물러선다 — 모르는 것을 안다고 말하지 않는다."""
        import inspect

        from updown.orchestration.walkforward import live_runner

        source = inspect.getsource(live_runner.LiveRunner)
        assert "pooled = self.account_modelled() if others > 0 else modelled" in source
        assert "if pooled is None:" in source
        assert '"code": "wallet_unattributable"' in source

    @pytest.mark.parametrize(
        ("pooled", "real", "cries"),
        [
            # 안 쓴 돈 — 사람은 계좌의 일부만 굴린다. 실측: 원장 합 1000 · 계정 4956.
            ("1000", "4956", False),
            ("1000", "1000", False),
            ("1000", "960", False),  # 5% 안쪽은 반올림·수수료 몫
            # 🔴 초과 — 원장이 계좌에 없는 돈을 센다. 그만큼의 주문이 거절된다.
            ("1000", "900", True),
            ("1000", "0", True),
        ],
    )
    def test_only_the_dangerous_direction_cries(self, pooled: str, real: str, cries: bool) -> None:
        """⚠️ 양방향으로 재면 *"계좌에 여유가 있다"* 가 매 걸음 error 로 뜬다.

        그러면 아무도 안 보게 되고, 그 밑에 진짜 초과가 묻힌다 (무방비 경보 전례).
        """
        from decimal import Decimal

        from updown.orchestration.walkforward.live_runner import WALLET_DRIFT

        p, r = Decimal(pooled), Decimal(real)
        assert (p > 0 and r >= 0 and p - r > p * WALLET_DRIFT) is cries
