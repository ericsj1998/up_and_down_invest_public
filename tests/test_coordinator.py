"""리밸런싱 조정자 (T61 M1) — 세션 예산 갱신·바스켓 관리.

막아야 하는 실패:
1. 🔴 각 세션에 잘못된 예산이 가는 것.
2. 🔴 바스켓 밖 세션의 돈이 총자본에서 빠지거나, 그 세션이 안 정리되는 것.
3. 🔴 바스켓 구성원인데 세션이 없는 것을 조정자가 못 알아채는 것.
"""

from decimal import Decimal

from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.portfolio.performance import TwrLedger


class FakePort:
    """테스트용 세션 포트 — 평가금액을 들고, 받은 예산을 기록한다."""

    def __init__(self, symbol: str, equity: Decimal) -> None:
        self._symbol = symbol
        self._equity = equity
        self.budget: Decimal | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    def equity(self) -> Decimal:
        return self._equity

    def set_budget(self, budget: Decimal) -> None:
        self.budget = budget


def _basket() -> Basket:
    return Basket(as_members([("BTC_USDT", Decimal(1)), ("ETH_USDT", Decimal(1))]))


def _coord(ports: dict[str, FakePort], start: int) -> Coordinator:
    engine = RebalanceEngine(basket=_basket(), ledger=TwrLedger(equity=Decimal(start)))
    return Coordinator(engine=engine, ports=dict(ports))  # type: ignore[arg-type]


def test_each_session_gets_its_target_budget() -> None:
    ports = {
        "BTC_USDT": FakePort("BTC_USDT", Decimal(120)),
        "ETH_USDT": FakePort("ETH_USDT", Decimal(80)),
    }
    coord = _coord(ports, 200)
    report = coord.tick()
    # 총 200 을 균등 → 각 100
    assert ports["BTC_USDT"].budget == Decimal(100)
    assert ports["ETH_USDT"].budget == Decimal(100)
    assert report.balance == Decimal(200)


def test_missing_basket_member_is_flagged() -> None:
    ports = {"BTC_USDT": FakePort("BTC_USDT", Decimal(100))}  # ETH 세션 없음
    coord = _coord(ports, 100)
    report = coord.tick()
    assert report.missing == ("ETH_USDT",)  # 조정자가 ETH 세션을 만들어야 한다


def test_symbol_outside_basket_winds_down() -> None:
    """바스켓 밖 종목은 총자본엔 들되 예산 0 을 받아 청산된다."""
    ports = {
        "BTC_USDT": FakePort("BTC_USDT", Decimal(100)),
        "ETH_USDT": FakePort("ETH_USDT", Decimal(100)),
        "DOGE_USDT": FakePort("DOGE_USDT", Decimal(50)),  # 바스켓에서 빠진 종목
    }
    coord = _coord(ports, 250)
    report = coord.tick()
    assert report.balance == Decimal(250)  # DOGE 50 포함
    assert ports["DOGE_USDT"].budget == Decimal(0)  # 청산 유도
    assert report.winding_down == ("DOGE_USDT",)
    # 남은 250 이 코어 둘에게 (각 125)
    assert ports["BTC_USDT"].budget == Decimal(125)


def test_twr_reported() -> None:
    ports = {
        "BTC_USDT": FakePort("BTC_USDT", Decimal(110)),
        "ETH_USDT": FakePort("ETH_USDT", Decimal(0)),
    }
    coord = _coord(ports, 100)  # 100 -> 110 = +10%
    report = coord.tick()
    assert report.twr_return == Decimal("0.1")
