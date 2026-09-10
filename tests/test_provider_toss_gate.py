"""토스 어댑터는 `UPDOWN_MARKETS` 에 토스 시장이 있을 때만 만든다 (2026-09-11 실측).

토스는 client 당 access token 이 하나라, 키를 가진 프로세스가 둘이면 서로를 무효화한다. 시장 목록이
`BINANCE` 뿐인 로컬 데모가 저평가 화면 준비로 토스 어댑터를 만들어 서버 시세를 끊었다 —
목록에 없으면 어댑터를 아예 만들지 않아야 토큰도 안 받는다.
"""

from __future__ import annotations

import pytest

from updown.common.domain.instrument import Market
from updown.marketdata.provider import (
    MarketDataProvider,
    UnsupportedMarketError,
    market_allowlist,
    toss_allowed,
)


def test_allowlist_parses_upper_and_trims() -> None:
    assert market_allowlist({"UPDOWN_MARKETS": " gate, nasdaq "}) == {"GATE", "NASDAQ"}
    assert market_allowlist({"UPDOWN_MARKETS": ""}) == frozenset()
    assert market_allowlist({}) == frozenset()


def test_toss_allowed_only_with_a_toss_market() -> None:
    assert toss_allowed(frozenset()) is True  # 제한 없음
    assert toss_allowed(frozenset({"GATE", "NASDAQ"})) is True
    assert toss_allowed(frozenset({"NYSE"})) is True
    assert toss_allowed(frozenset({"BINANCE"})) is False
    assert toss_allowed(frozenset({"NONE"})) is False


def test_binance_only_process_never_builds_a_toss_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDOWN_MARKETS", "BINANCE")
    monkeypatch.setattr(MarketDataProvider, "_shared_toss", None)
    with pytest.raises(UnsupportedMarketError, match="UPDOWN_MARKETS"):
        MarketDataProvider().adapter_for(Market.NASDAQ)
    assert MarketDataProvider._shared_toss is None  # pyright: ignore[reportPrivateUsage]
    assert "NASDAQ" not in MarketDataProvider().live_markets()
