"""매매법이 선언하지 않은 갈래로는 판을 띄우지 않는다 (2026-09-11).

Note:
    🔴 사용자 신고: 주식 콘솔에서 펀드를 만들자 `TOSS 요청이 상한 300 을 넘었다` 로 503.

    화면이 묶음을 주식으로 바꿔도 고른 매매법은 코인 것으로 남아 있었다 — `<select>` 는
    목록에 없는 값을 받으면 **첫 항목처럼** 그리므로 사람 눈에는 주식 매매법이 골라진 것처럼
    보였다. 서버에는 그 값을 그대로 보냈고, 판은 코인 매매법으로 NASDAQ 에 떴다.
    선언 축과 봉이 코인 기준이라 첫 시드가 비싸졌고, 사람은 원인을 알 수 없는 503 만 봤다.

    화면은 고쳤지만(묶음이 바뀌면 매매법도 맞춘다) 문은 서버에 있어야 한다 — API 를 직접
    부르거나 옛 화면이 남아 있어도 같은 일이 일어나면 안 된다.
"""

from __future__ import annotations

import pytest

from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import Playbook
from updown.apps.api.walkforward import playbooks_outside
from updown.common.domain.instrument import Market, MarketGroup


def _book(name: str) -> Playbook:
    found = next((item for item in load_playbooks() if item.playbook_id == name), None)
    assert found is not None, f"{name} 선언이 없다 — 시험이 낡았다"
    return found


def test_coin_playbook_is_refused_on_a_stock_market() -> None:
    """코인 매매법 + NASDAQ = 갈래 밖. 신고된 그 조합이다."""
    book = _book("private_strategy")
    assert MarketGroup.COIN in book.market_groups
    assert playbooks_outside([book], Market.NASDAQ) == ["private_strategy"]
    assert playbooks_outside([book], Market.NYSE) == ["private_strategy"]
    assert playbooks_outside([book], Market.KRX) == ["private_strategy"]


def test_stock_playbook_passes_on_its_own_market_and_fails_on_coin() -> None:
    """주식 견본은 해외주식에서만 돈다."""
    book = _book("sample_stock_ma_cross")
    assert playbooks_outside([book], Market.NASDAQ) == []
    assert playbooks_outside([book], Market.GATE) == ["sample_stock_ma_cross"]


def test_chart_order_playbook_runs_everywhere() -> None:
    """차트 주문(`custom`)은 세 갈래를 다 선언한다 — 가드가 주문 창을 막으면 안 된다."""
    book = _book("custom")
    for market in (Market.GATE, Market.BINANCE, Market.NASDAQ, Market.NYSE, Market.KRX):
        assert playbooks_outside([book], market) == [], market.value


def test_every_listed_playbook_declares_a_group() -> None:
    """올라온 매매법은 갈래 선언이 비어 있지 않다 — 비면 어디서도 못 뜬다."""
    empty = [
        item.playbook_id for item in load_playbooks() if item.listed and not item.market_groups
    ]
    assert empty == []


@pytest.mark.parametrize(
    ("market", "group"),
    [
        (Market.GATE, MarketGroup.COIN),
        (Market.BINANCE, MarketGroup.COIN),
        (Market.NASDAQ, MarketGroup.FOREIGN_STOCK),
        (Market.NYSE, MarketGroup.FOREIGN_STOCK),
        (Market.KRX, MarketGroup.DOMESTIC_STOCK),
    ],
)
def test_market_maps_to_the_group_the_guard_uses(market: Market, group: MarketGroup) -> None:
    """가드가 읽는 갈래가 시장마다 우리가 아는 그것이다."""
    assert MarketGroup.of(market) is group


def test_mixed_set_names_only_the_outsiders() -> None:
    """세트 중 갈래 밖인 것만 이름이 나온다 — 사람이 무엇을 바꿔야 하는지 알아야 한다."""
    got = playbooks_outside([_book("custom"), _book("private_strategy")], Market.NASDAQ)
    assert got == ["private_strategy"]
